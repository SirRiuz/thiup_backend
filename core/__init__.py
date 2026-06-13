# Load the Celery app when Django starts so that @shared_task definitions
# get registered (standard celery + django pattern).
from core.celery import app as celery_app

__all__ = ("celery_app",)
