# Django
from django.db.models import Count, DateField, ExpressionWrapper, F, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.db.models.query import QuerySet
from django.utils import timezone

from app.constants.threads import FEED_TOP_REPLY_MIN_REACTORS, FEED_TOP_REPLY_ROOT_RATIO

# Models
from app.models.thread import Thread


def get_ranked_thread() -> QuerySet[Thread]:
    """
    Add relevance points to each thread
    """
    threads = (
        Thread.objects.filter(is_active=True)
        .annotate(
            reaction_count=Count("reactionrelation__thread__id"),
            days_since_creation=ExpressionWrapper(timezone.now() - F("create_at__date"), output_field=DateField()),
        )
        .annotate(
            days_since_creation=ExpressionWrapper(F("days_since_creation__day"), output_field=IntegerField()),
            sub_threads_count=Coalesce(
                Subquery(
                    Thread.objects.filter(sub=OuterRef("pk"))
                    .values("sub")
                    .annotate(count=Count("id"))
                    .values("count")[:1]
                ),
                0,
            ),
            index=((F("reaction_count") * 0.4 + F("sub_threads_count") * 0.6) - F("days_since_creation") * 0.3),
        )
    )

    return threads


def with_card_relations(queryset, request_mask) -> QuerySet[Thread]:
    """
    Preloads EVERYTHING that ThreadSerializer.to_representation needs per
    thread, for lists (feed/search/tag/replies) WITHOUT N+1:

      - select_related mask and sub (before: 2-3 queries per post)
      - responses_count_db via Subquery (before: 1 COUNT per post)
      - active_media prefetched (before: 1 query per post)
      - prefetched_reactions: ALL active relations with their reaction
        (before: 1 aggregate query per post + 1 COUNT per reaction)
      - my_reaction_relations: the current mask's reaction (before: 1 per post)

    The serializer uses these attributes if present (fast path) and falls
    back to the legacy queries if not (retrieve/create keep working unchanged).
    Measured result: the feed goes from ~194 queries to a constant number.
    """
    # Local import to avoid cycles (reactions imports threads).
    from django.db.models import Prefetch

    from app.models.media import ThreadFile
    from app.models.reaction_relation import ReactionRelation

    return (
        queryset.select_related("mask", "sub")
        .annotate(
            responses_count_db=Coalesce(
                Subquery(
                    Thread.objects.filter(is_active=True, sub=OuterRef("pk"))
                    .order_by()
                    .values("sub")
                    .annotate(c=Count("pk"))
                    .values("c")[:1]
                ),
                0,
            )
        )
        .prefetch_related(
            Prefetch(
                "threadfile_set",
                queryset=ThreadFile.objects.filter(is_active=True),
                to_attr="active_media",
            ),
            Prefetch(
                "reactionrelation_set",
                queryset=ReactionRelation.objects.filter(is_active=True).select_related("reaction"),
                to_attr="prefetched_reactions",
            ),
            Prefetch(
                "reactionrelation_set",
                queryset=ReactionRelation.objects.filter(is_active=True, mask=request_mask).select_related("reaction"),
                to_attr="my_reaction_relations",
            ),
        )
    )


def attach_top_replies(cards, page, request_mask) -> None:
    """
    Feed conversation preview (X-style two-story card): attaches ONE direct
    reply per root card as `top_reply`, decided by two rules IN CASCADE —
    exactly X's semantics:

      A. SELF-THREAD (priority): the root author continued their own thread
         (reply.mask == root.mask). The author's FIRST chronological
         continuation shows, with NO engagement threshold — "the author's
         thread doesn't compete with anything". Detected server-side by mask
         equality (the same boolean-only privacy contract as is_op).
      B. EARNED (fallback, only when A doesn't apply): the BEST third-party
         reply — two gates, BOTH required (see app/constants/threads.py):
         >= FEED_TOP_REPLY_MIN_REACTORS unique reactors excluding the
         reply's own author (absolute floor, anti-self-boost), AND >=
         FEED_TOP_REPLY_ROOT_RATIO × the root's precomputed
         unique_reactors_count (relative bar — keeps the preview scarce at
         scale; self-calibrating as threads grow). Best = most unique
         reactors, tie → newest; if the best fails the relative bar, every
         other reply fails it too — the card carries no preview.

    Shared by EVERY feed surface that serves root cards (For You, Close You,
    /threads/ list, /threads/mine/ and the search posts tab) — never the
    thread view (/responses/ shows the real tree). Cost: TWO small grouped
    queries for the whole page (indexed sub_id lookups) + one
    with_card_relations batch for the winners (fast-path serialization).
    `cards` (the serialized page, same order as `page`) is mutated in place.
    """
    # Local import: the serializer module imports THIS module at load time.
    from app.rest.serializers.thread_serializer import ThreadSerializer

    if not page:
        return
    now_date = timezone.localtime(timezone.now())
    root_ids = [t.id for t in page]
    alive = Q(expire_date__gte=now_date) | Q(expire_date__isnull=True)

    # ── Rule A: the author's own continuation (first chronological) ───────
    # mask == sub.mask never matches NULL masks (SQL NULL equality) → legacy
    # authorless rows fall through to rule B naturally.
    self_replies = (
        Thread.objects.filter(
            alive,
            sub_id__in=root_ids,
            is_active=True,
            visibility=True,
            mask__isnull=False,
            mask=F("sub__mask"),
        )
        .order_by("sub_id", "create_at")
        .values_list("id", "sub_id")
    )
    best_by_root = {}
    for reply_id, root_id in self_replies:
        # Ordered by (root, create_at): the first hit per root IS the
        # author's earliest continuation.
        best_by_root.setdefault(root_id, reply_id)

    # ── Rule B: best earned third-party reply, only where A didn't fire ───
    pending_ids = [i for i in root_ids if i not in best_by_root]
    if pending_ids:
        root_by_id = {t.id: t for t in page}
        candidates = (
            Thread.objects.filter(
                alive,
                sub_id__in=pending_ids,
                is_active=True,
                visibility=True,
            )
            .annotate(
                unique_reactors=Count(
                    "reactionrelation__mask",
                    distinct=True,
                    filter=Q(reactionrelation__is_active=True)
                    # Legacy rows may have a null author mask — then nothing
                    # can be excluded and every reactor counts.
                    & (Q(mask__isnull=True) | ~Q(reactionrelation__mask=F("mask"))),
                )
            )
            .filter(unique_reactors__gte=FEED_TOP_REPLY_MIN_REACTORS)
            .order_by("sub_id", "-unique_reactors", "-create_at")
            .values_list("id", "sub_id", "unique_reactors")
        )
        decided = set()
        for reply_id, root_id, reactors in candidates:
            # Ordered by (root, -reactors, -create_at): the first hit per
            # root has the MOST reactors — if IT fails the relative bar, no
            # other reply of that root can pass it, so the root is decided
            # either way.
            if root_id in decided:
                continue
            decided.add(root_id)
            root = root_by_id.get(root_id)
            # The root's unique reactors come PRECOMPUTED by the momentum
            # cron (0 for brand-new posts → the relative bar passes
            # trivially and the absolute floor governs).
            root_reactors = getattr(root, "unique_reactors_count", 0) or 0
            if reactors >= FEED_TOP_REPLY_ROOT_RATIO * root_reactors:
                best_by_root[root_id] = reply_id
    if not best_by_root:
        return

    winners = with_card_relations(Thread.objects.filter(id__in=best_by_root.values()), request_mask)
    winner_by_id = {t.id: t for t in winners}

    for thread, card in zip(page, cards):
        reply_id = best_by_root.get(thread.id)
        if reply_id is None or reply_id not in winner_by_id:
            continue
        # op_mask = the root author's mask, so the preview can show the
        # "· Author" meta when the OP replied — boolean only, same
        # thread-local privacy contract as /responses/.
        card["top_reply"] = ThreadSerializer(
            winner_by_id[reply_id],
            many=False,
            context=({"mask": request_mask, "op_mask": thread.mask, "short": True}),
        ).data
