# flake8: noqa
# Import all tasks so Celery's autodiscover registers them.
from app.tasks.momentum import recompute_momentum
