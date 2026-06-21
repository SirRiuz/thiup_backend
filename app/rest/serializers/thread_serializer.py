# Python
import datetime

# Django
from rest_framework import serializers
from django.utils import timezone
from django.db.models import Count

# Models
from app.models.thread import Thread
from app.models.media import ThreadFile
from app.models.reaction_relation import ReactionRelation
from app.models.reaction import Reaction


# Serializers
from app.rest.serializers.reaction_serializer import ReactionSerializer
from app.rest.serializers.media_serializer import (
    ThreadMediaSerializer,
    MediaFileSerializer
)

# Libs
from app.utils.time import format_short_time
from app.methods.files import save_files
from app.models.mask import Mask
from app.methods.tags import create_tags, get_tags_list
from app.rest.serializers.mask_serializer import MaskSerializer


class ThreadSerializer(serializers.ModelSerializer):

    content = serializers.JSONField(required=True)
    media = serializers.ListField(
        required=False, child=MediaFileSerializer(
            required=False))
    sub = serializers.SlugRelatedField(
        slug_field="uid",
        required=False,
        allow_null=True,
        queryset=Thread.objects.filter(is_active=True),
        help_text="Parent thread, referenced by its uid.")

    def create(self, validated_data):
        data = validated_data.copy()
        if "media" in data:
            del data["media"]

        mask = self.context["mask"]
        # language/region come in validated_data: the view injects them
        # via serializer.save(...) already normalized from what the
        # frontend DECLARED (navigator.language) — without GeoIP.
        obj = super().create({"mask": mask, **data})
        media_data = validated_data.get("media", [])
        tags = get_tags_list(data["text"])

        create_tags(obj, tags)
        save_files(files=media_data, thread=obj)
        return obj

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        head_id = instance.sub.uid if instance.sub else None

        # ── FAST PATH (lists: feed/search/tag/replies) ─────────────────────
        # If the queryset came through with_card_relations(), the per-thread
        # data is already prefetched/annotated — ZERO extra queries per post. If
        # not (retrieve/create), each block falls back to the equivalent legacy
        # query.

        # responses_count: annotated by Subquery vs COUNT per post.
        responses_count = getattr(instance, "responses_count_db", None)
        if responses_count is None:
            responses_count = Thread.objects.filter(
                is_active=True, sub=instance).count()
        representation["responses_count"] = responses_count

        # media: prefetch (active_media) vs query per post.
        media = getattr(instance, "active_media", None)
        if media is None:
            media = ThreadFile.objects.filter(
                is_active=True, thread=instance)
        representation["media"] = ThreadMediaSerializer(
            media, many=True).data

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
            # Same as the legacy: number of reaction TYPES of the thread.
            representation["reactions_count"] = len(ordered)

            my_relations = getattr(instance, "my_reaction_relations", [])
            last_reaction = my_relations[0].reaction if my_relations else None
        else:
            thread_reactions = Reaction.objects.filter(
                is_active=True,
                reactionrelation__thread=instance).\
                annotate(reaction_count=Count(
                    'reactionrelation')).order_by('-reaction_count')
            representation["reactions_count"] = thread_reactions.count()
            representation["reactions"] = ReactionSerializer(
                thread_reactions, many=True, context=({
                    "thread": instance})).data

            last_relation = ReactionRelation.objects.filter(
                thread=instance,
                is_active=True,
                mask=self.context["mask"]
            )
            last_reaction = last_relation[0].reaction if last_relation else None

        representation["parent"] = head_id
        representation["last_reaction"] = ReactionSerializer(last_reaction, many=False).data if \
            last_reaction else None

        if self.context.get("show_responses"):
            # We propagate show_responses to serialize the COMPLETE tree of
            # replies (level 2, 3, 4, ... N). Without this, the replies of a
            # sub-reply were never included and replies of 3rd level+ were
            # created in DB but didn't come back in the GET (they stayed in "limbo").
            # It's a tree (FK sub, no cycles): the recursion terminates on its own.
            subs = Thread.objects.filter(is_active=True, sub=instance)
            representation["responses"] = ThreadSerializer(
                subs,
                many=True,
                context=({
                    "mask": self.context["mask"],
                    "short": True,
                    "show_responses": True})).data

        mask_data = MaskSerializer(instance.mask, many=False).data if \
            instance.mask else None

        # momentum_final (For You): the momentum ALREADY boosted by region,
        # computed at query time in the action — it only exists in the
        # For You feed. The persisted field (momentum_score) remains
        # the base: if the regions differ, final == base.
        momentum_final = getattr(instance, "momentum_final", None)
        if momentum_final is not None:
            representation["momentum_final"] = momentum_final

        representation["mask"] = mask_data
        representation["is_new"] = instance.is_new()
        representation["is_op"] = instance.mask == self.context["mask"]
        representation["create_at"] = format_short_time(instance.create_at)
        # Absolute publish timestamp (ISO 8601) for the "thread details" panel.
        # `create_at` above is the compact RELATIVE string used by the card; this
        # is the raw timestamp so the client can render a localized date. Public
        # thread metadata, no extra query (the column is already loaded).
        representation["created_at_iso"] = (
            instance.create_at.isoformat() if instance.create_at else None)

        return representation

    class Meta:
        model = Thread
        # The threshold counters are internal plumbing of the For You engine.
        # momentum_score IS exposed: the frontend shows it in the card
        # when DEBUG is active (src/settings.js) — it's an innocuous float.
        # language/region are excluded from the contract: the client DECLARES
        # them in the creation payload but the view normalizes them and passes
        # them via serializer.save(...) — they are not editable serializer fields
        # nor part of the card.
        # text_norm is a derived search column (lowercase, accent-stripped
        # copy of text) — internal plumbing, never part of the card.
        exclude = ("id", "is_active", "update_at",
                   "visibility", "expire_date", "region", "language",
                   "geohash", "text_norm",
                   "unique_reactors_count",
                   "unique_commenters_count")
