# Django
from django.utils import timezone
from django.db.models.query import QuerySet
from django.db.models.functions import Coalesce
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.db.models import F, ExpressionWrapper, IntegerField, DateField, Count


# Models
from apps.threads.models.thread import Thread


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
