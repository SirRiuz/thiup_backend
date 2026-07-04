# flake8: noqa
# One file per domain, star-imported here so a single `import app.signals`
# (in AppConfig.ready) connects every receiver — same layout as the signals
# package convention used across our Django projects.
from app.signals.media_signals import *  # noqa: F401, F403
