# Python
import logging

# Celery
from celery import shared_task

# Django
from django.core.management import call_command


LOGGER = logging.getLogger(__name__)


@shared_task
def recompute_momentum():
    """
    Recomputes momentum_score + counters for For You.

    Scheduled by Celery beat every 10 min (CELERY_BEAT_SCHEDULE in
    core/settings.py). The logic lives in the management command
    `recompute_momentum` — it remains runnable by hand
    (make recompute_momentum) for debug or backfill.
    """
    LOGGER.info("task recompute_momentum: recibida desde beat/broker")
    call_command("recompute_momentum")
