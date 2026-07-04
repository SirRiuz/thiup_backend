from django.apps import AppConfig


class MainAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app"

    def ready(self):
        # Connect every receiver in the signals package (one file per domain,
        # star-imported by app/signals/__init__.py).
        import app.signals  # noqa: F401
