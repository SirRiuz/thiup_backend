# Python
import mimetypes

# Django
from rest_framework import serializers

# Models
from apps.threads.models.media import ThreadFile


class MediaFileSerializer(serializers.Serializer):
    type = serializers.CharField()
    data = serializers.CharField()

class ThreadMediaSerializer(serializers.ModelSerializer):
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        extension = mimetypes.guess_type(instance.file.url)
        
        representation["type"] = (extension[0] if extension \
            else "Unknow")
        
        representation.pop("create_at")
        representation.pop("update_at")
        representation.pop("thread")
        return representation
    
    class Meta:
        model = ThreadFile
        exclude = ("is_active",)
