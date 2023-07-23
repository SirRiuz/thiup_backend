# Django
from rest_framework import serializers

# Models
from apps.threads.models.thread import Thread
from apps.threads.models.media import ThreadFile

# Serializers
from apps.threads.serializers.media_serializer import (
    ThreadMediaSerializer,
    MediaFileSerializer
)

# Libs
from apps.threads.methods.files import save_files


class ThreadSerializer(serializers.ModelSerializer):
    
    media = serializers.ListField(
        required=False, child=MediaFileSerializer(
            required=False))
    
    def create(self, validated_data):
        data = validated_data.copy()
        if "media" in data:
            del data["media"]
            
        obj = super().create(data)
        media_data = validated_data.get("media", [])
        save_files(files=media_data, thread=obj)
        return obj
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        subs = Thread.objects.filter(is_active=True, sub=instance)
        media = ThreadFile.objects.filter(
            is_active=True, thread=instance)
        
        representation["responses_count"] = subs.count()
        representation["media"] = ThreadMediaSerializer(
            media, many=True).data
        
        if not self.context.get("short"):
            representation["responses"] = ThreadSerializer(
                subs, many=True).data

        representation.pop("sub")
        representation.pop("create_at")
        representation.pop("update_at")
        return representation

    class Meta:
        model = Thread
        exclude = ('is_active',)
