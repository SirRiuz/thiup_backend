from django.conf import settings
from django.core.files.storage import FileSystemStorage


class AbsoluteUrlFileSystemStorage(FileSystemStorage):
    """Local FileSystemStorage that returns absolute URLs.

    Pairs with the cloud (django-storages S3/R2) backend which already emits
    absolute bucket URLs, so consumers see the same shape regardless of where
    files live. Uses settings.MEDIA_BASE_URL as the origin; falls back to the
    parent's relative URL if MEDIA_BASE_URL is empty.
    """

    def url(self, name: str) -> str:
        relative = super().url(name)
        base = getattr(settings, "MEDIA_BASE_URL", "")
        if not base:
            return relative
        return f"{base.rstrip('/')}/{relative.lstrip('/')}"
