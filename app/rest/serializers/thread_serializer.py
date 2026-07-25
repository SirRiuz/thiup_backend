# Django
from django.db.models import Count
from rest_framework import serializers

from app.constants.threads import THREAD_TEXT_MAX_LENGTH
from app.methods.tags import create_tags, get_tags_list
from app.methods.threads import with_card_relations
from app.models.media import ThreadFile
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation

# Models
from app.models.thread import Thread
from app.rest.serializers.mask_serializer import MaskSerializer
from app.rest.serializers.media_serializer import ThreadMediaSerializer

# Serializers
from app.rest.serializers.reaction_serializer import ReactionSerializer

# Libs
from app.utils.time import format_short_time


class ThreadSerializer(serializers.ModelSerializer):
    # write_only: both are CREATE inputs the frontend never reads back —
    # `content` (the DraftJS JSON) was the heaviest field of every card and
    # the FE renders from `text`; the reply nesting comes from `responses`,
    # not from echoing `sub`. Payload trimmed, validation/create unchanged.
    content = serializers.JSONField(required=True, write_only=True)
    # Server-side length gate (Threads' exact 500, posts AND replies): the FE
    # composers block it first, but the API is the real limit — without this
    # a raw client could post unbounded text (the model is a plain TextField).
    # trim_whitespace=False: the composers already trim; the serializer must
    # not silently mutate what a client sent. Input-only — legacy longer rows
    # keep serializing.
    text = serializers.CharField(
        required=True,
        max_length=THREAD_TEXT_MAX_LENGTH,
        trim_whitespace=False,
        help_text="Text of the thread.",
    )
    sub = serializers.SlugRelatedField(
        slug_field="uid",
        required=False,
        allow_null=True,
        write_only=True,
        queryset=Thread.objects.filter(is_active=True),
        help_text="Parent thread, referenced by its uid.",
    )

    def create(self, validated_data):
        data = validated_data.copy()

        mask = self.context["mask"]
        # language/region come in validated_data: the view injects them
        # via serializer.save(...) already normalized from what the
        # frontend DECLARED (navigator.language) — without GeoIP.
        obj = super().create({"mask": mask, **data})
        tags = get_tags_list(data["text"])

        create_tags(obj, tags)
        # Media is attached separately via the presign/confirm upload flow
        # (app/rest/thread_files.py), not embedded in the create payload.
        return obj

    def to_representation(self, instance):
        representation = super().to_representation(instance)

        # ── FAST PATH (lists: feed/search/tag/replies) ─────────────────────
        # If the queryset came through with_card_relations(), the per-thread
        # data is already prefetched/annotated — ZERO extra queries per post. If
        # not (retrieve/create), each block falls back to the equivalent legacy
        # query.

        # responses_count: annotated by Subquery vs COUNT per post.
        responses_count = getattr(instance, "responses_count_db", None)
        if responses_count is None:
            responses_count = Thread.objects.filter(
                is_active=True,
                sub=instance,
            ).count()
        representation["responses_count"] = responses_count

        # media: prefetch (active_media) vs query per post.
        media = getattr(instance, "active_media", None)
        if media is None:
            media = ThreadFile.objects.filter(is_active=True, thread=instance)
        representation["media"] = ThreadMediaSerializer(media, many=True).data

        # reactions (+counts) and last_reaction of the current mask: grouped in
        # Python from the prefetched relations vs 2 + N_reactions queries
        # per post (the ReactionSerializer with thread context did a COUNT
        # for each reaction).
        prefetched = getattr(instance, "prefetched_reactions", None)
        if prefetched is not None:
            groups = {}
            for relation in prefetched:
                reaction = relation.reaction
                if reaction is None or not reaction.is_active:
                    continue
                entry = groups.setdefault(reaction.id, [reaction, 0])
                entry[1] += 1
            ordered = sorted(groups.values(), key=lambda e: -e[1])

            reactions_data = []
            for reaction, count in ordered:
                item = ReactionSerializer(reaction, many=False).data
                item["reaction_count"] = count
                reactions_data.append(item)
            representation["reactions"] = reactions_data

            my_relations = getattr(instance, "my_reaction_relations", [])
            last_reaction = my_relations[0].reaction if my_relations else None
        else:
            thread_reactions = (
                Reaction.objects.filter(
                    is_active=True,
                    reactionrelation__thread=instance,
                )
                .annotate(reaction_count=Count("reactionrelation"))
                .order_by("-reaction_count")
            )
            representation["reactions"] = ReactionSerializer(
                thread_reactions,
                many=True,
                context=({"thread": instance}),
            ).data

            last_relation = ReactionRelation.objects.filter(
                thread=instance,
                is_active=True,
                mask=self.context["mask"],
            )
            last_reaction = last_relation[0].reaction if last_relation else None

        representation["last_reaction"] = (
            ReactionSerializer(
                last_reaction,
                many=False,
            ).data
            if last_reaction
            else None
        )

        if self.context.get("show_responses"):
            # We propagate show_responses to serialize the COMPLETE tree of
            # replies (level 2, 3, 4, ... N). Without this, the replies of a
            # sub-reply were never included and replies of 3rd level+ were
            # created in DB but didn't come back in the GET (they stayed in "limbo").
            # It's a tree (FK sub, no cycles): the recursion terminates on its own.
            # with_card_relations: each nested reply serializes on the FAST
            # path (before this, every node fell back to the legacy queries —
            # ~5 extra queries per nested reply, hundreds per deep page).
            subs = with_card_relations(
                Thread.objects.filter(is_active=True, sub=instance),
                self.context.get("mask"),
            )
            representation["responses"] = ThreadSerializer(
                subs,
                many=True,
                context=(
                    {
                        "mask": self.context["mask"],
                        # Propagate the OP mask so nested sub-replies also compute
                        # is_op against the thread author (local to this thread).
                        "op_mask": self.context.get("op_mask"),
                        "short": True,
                        "show_responses": True,
                    }
                ),
            ).data

        mask_data = (
            MaskSerializer(
                instance.mask,
                many=False,
            ).data
            if instance.mask
            else None
        )

        # momentum_final (For You): the momentum ALREADY boosted by region,
        # computed at query time in the action — it only exists in the
        # For You feed. The persisted field (momentum_score) remains
        # the base: if the regions differ, final == base.
        momentum_final = getattr(instance, "momentum_final", None)
        if momentum_final is not None:
            representation["momentum_final"] = momentum_final

        representation["mask"] = mask_data
        # is_mine: this thread/reply belongs to the CURRENT viewer (anonymous
        # mask comparison). PRIVATE — only ever true for the viewer's own
        # content, so only its author sees it; reveals nothing to third parties.
        # Drives the feed/search "Your thread" tag. Only a boolean is sent.
        representation["is_mine"] = instance.mask == self.context.get("mask")
        # is_op: this reply's author IS the thread's Original Poster. Computed
        # ONLY inside a thread — `op_mask` (the root thread's author mask) is put
        # in context by the responses endpoint — so it is LOCAL to the thread and
        # never exposes a reusable author id: only a boolean is sent, with no
        # cross-thread correlation. False when op_mask is absent (feed/search).
        op_mask = self.context.get("op_mask")
        representation["is_op"] = bool(op_mask is not None and instance.mask == op_mask)
        representation["create_at"] = format_short_time(instance.create_at)
        # Absolute publish timestamp (ISO 8601) for the "thread details" panel.
        # `create_at` above is the compact RELATIVE string used by the card; this
        # is the raw timestamp so the client can render a localized date. Public
        # thread metadata, no extra query (the column is already loaded).
        representation["created_at_iso"] = instance.create_at.isoformat() if instance.create_at else None

        return representation

    class Meta:
        model = Thread
        # The threshold counters are internal plumbing of the For You engine.
        # language/region are excluded from the contract: the client DECLARES
        # them in the creation payload but the view normalizes them and passes
        # them via serializer.save(...) — they are not editable serializer fields
        # nor part of the card.
        # text_norm is a derived search column (lowercase, accent-stripped
        # copy of text) — internal plumbing, never part of the card.
        # geohash4 is the derived ~39 km cell of the Close You wide filter:
        # location metadata that must never ride on a public card.
        # mask is EXCLUDED as an input field: authorship is forced server-side
        # from request.mask in the view; a writable `mask` let a client
        # override or null out the author (mass assignment). It is still
        # emitted by to_representation (built from the instance, not this field).
        exclude = (
            "id",
            "is_active",
            "update_at",
            "visibility",
            "expire_date",
            "region",
            "language",
            "geohash",
            "geohash4",
            "text_norm",
            "unique_reactors_count",
            "unique_commenters_count",
            "mask",
        )
        # momentum_score IS exposed (the FE shows it under DEBUG) but must be
        # READ-ONLY: it is precomputed by the momentum cron, and a writable
        # field let a client set it to top the feed/search ranking.
        read_only_fields = ("momentum_score",)
