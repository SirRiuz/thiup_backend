# Python
import mimetypes

# Django
from rest_framework import serializers

# Models
from apps.threads.models.media import ThreadFile


class ResolutionSerializer(serializers.Serializer):
    width = serializers.IntegerField()
    height = serializers.IntegerField()


class MediaFileSerializer(serializers.Serializer):
    type = serializers.CharField(default=False)
    resolution = ResolutionSerializer(required=True)
    is_video = serializers.BooleanField(default=False)
    target_color = serializers.CharField(default="#161c1e")
    data = serializers.CharField(required=True, allow_null=False)


class ThreadMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThreadFile
        exclude = (
            "is_active",
            "create_at",
            "update_at",
            "thread"
        )
