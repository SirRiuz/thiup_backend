# Django
from rest_framework import serializers

# Models
from app.models.media import ThreadFile

# Libs
from app.methods.storage_backends import get_backend


class ThreadMediaSerializer(serializers.ModelSerializer):
    """Public representation of a thread's media. Exposes only safe fields plus
    `file` (the public URL derived from the stored key by the storage adapter)
    and `target_color` (the dominant color, used as the fullscreen viewer's
    background). Dimensions and the rest of `metadata` stay internal — only the
    single color value is surfaced; the raw key and metadata never leave the API."""

    file = serializers.SerializerMethodField()
    target_color = serializers.SerializerMethodField()

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

    class Meta:
        model = ThreadFile
        fields = ("id", "uid", "is_video", "is_nsfw", "file", "target_color")
