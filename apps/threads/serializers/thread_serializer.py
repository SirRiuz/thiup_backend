# Python
import datetime

# Django
from rest_framework import serializers
from django.utils import timezone
from django.db.models import Count

# Models
from apps.threads.models.thread import Thread
from apps.threads.models.media import ThreadFile
from apps.reactions.models.reaction_relation import ReactionRelation
from apps.reactions.models.reaction import Reaction


# Serializers
from apps.reactions.serializers.reaction_serializer import ReactionSerializer
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
        now_date = timezone.now()
        head_id = instance.sub.id if instance.sub else None
        subs = Thread.objects.filter(is_active=True, sub=instance)
        media = ThreadFile.objects.filter(
            is_active=True, thread=instance)
        thread_reactions = Reaction.objects.filter(
            is_active=True,
            reactionrelation__thread=instance).\
                annotate(reaction_count=Count(
                    'reactionrelation')).\
                        order_by('-reaction_count', 'id')
        
        representation["parent"] = head_id
        #representation["continue_secuence"] = head_id == self.context.get("head")
        representation["responses_count"] = subs.count()
        representation["short_id"] = str(instance.id)[0:5]
        representation["is_expired"] = (now_date > instance.expire_date if \
            instance.expire_date else None)
        representation["media"] = ThreadMediaSerializer(
            media, many=True).data


        representation["reactions"] = ReactionSerializer(
            thread_reactions,
            many=True,
            context=({"thread": instance})).data
        
        if not self.context.get("short"):
            representation["responses"] = ThreadSerializer(
                subs,
                many=True,
                context=({
                    "head": instance.id})).data

        representation.pop("sub")
        representation.pop("create_at")
        representation.pop("update_at")
        return representation

    class Meta:
        model = Thread
        exclude = ('is_active',)
