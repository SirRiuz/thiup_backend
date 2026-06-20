# Django
from django.utils import timezone
from django.db.models.query import QuerySet
from django.db.models.functions import Coalesce
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.db.models import F, ExpressionWrapper, IntegerField, DateField, Count


# Models
from app.models.thread import Thread


def get_ranked_thread() -> QuerySet[Thread]:
    """
    Add relevance points to each thread
    """
    threads = Thread.objects.filter(is_active=True).annotate(
        reaction_count=Count("reactionrelation__thread__id"),
        days_since_creation=ExpressionWrapper(
            timezone.now() - F('create_at__date'), 
            output_field=DateField()
        )
    ).annotate(
        days_since_creation=ExpressionWrapper(
            F("days_since_creation__day"), 
            output_field=IntegerField()
        ),
        sub_threads_count=Coalesce(Subquery(Thread.objects.filter(
            sub=OuterRef("pk")).values("sub").annotate(
                count=Count("id")).values("count")[:1]), 0),

        index=((F("reaction_count") * .4 + F("sub_threads_count")
                * .6) - F("days_since_creation") * .3)
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
    from app.models.media import ThreadFile
    from app.models.reaction_relation import ReactionRelation
    from django.db.models import Prefetch

    return (
        queryset
        .select_related("mask", "sub")
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
                queryset=ReactionRelation.objects.filter(
                    is_active=True
                ).select_related("reaction"),
                to_attr="prefetched_reactions",
            ),
            Prefetch(
                "reactionrelation_set",
                queryset=ReactionRelation.objects.filter(
                    is_active=True, mask=request_mask
                ).select_related("reaction"),
                to_attr="my_reaction_relations",
            ),
        )
    )
