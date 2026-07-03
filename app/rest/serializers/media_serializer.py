# Django
from rest_framework import serializers

# Models
from app.models.media import ThreadFile

# Libs
from app.methods.storage_backends import get_backend


class ThreadMediaSerializer(serializers.ModelSerializer):
    """Public representation of a thread's media. Exposes only safe fields plus
    `file` (the public URL derived from the stored key by the storage adapter),
    `target_color` (the dominant color, used as the fullscreen viewer's
    background) and `width`/`height` (intrinsic dimensions, so the client can
    reserve the exact aspect-ratio BEFORE the media loads — no layout jump).
    All three are surfaced FROM `metadata` (they are not columns); the rest of
    the metadata JSON and the raw key never leave the API."""

    file = serializers.SerializerMethodField()
    target_color = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()

    def get_file(self, obj):
        # Prefer the persisted full public URL; fall back to deriving it from
        # the key (e.g. legacy rows saved before file_url existed).
        if obj.file_url:
            return obj.file_url
        if obj.file_key:
            request = self.context.get("request")
            base_url = request.build_absolute_uri("/") if request else None
            return get_backend().public_url(obj.file_key, base_url=base_url)
        return None

    def get_target_color(self, obj):
        # Only the dominant color hex from metadata — nothing else from it.
        color = (obj.metadata or {}).get("target_color")
        return color if isinstance(color, str) else None

    @staticmethod
    def _dimension(obj, key):
        # Dimensions live INSIDE metadata (stored as ints at confirm). Coerced
        # defensively — 0 means "unknown" (legacy rows / failed client probe)
        # and the client falls back to measuring on load, as before.
        value = (obj.metadata or {}).get(key)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def get_width(self, obj):
        return self._dimension(obj, "width")

    def get_height(self, obj):
        return self._dimension(obj, "height")

    class Meta:
        model = ThreadFile
        fields = (
            "id",
            "uid",
            "is_video",
            "is_nsfw",
            "file",
            "target_color",
            "width",
            "height",
        )
