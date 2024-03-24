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
import humanize
from apps.threads.methods.files import save_files
from apps.masks.models.mask_model import Mask
from apps.tags.methods.tags import create_tags, get_tags_list
from apps.masks.serializers.mask_serializer import MaskSerializer


class ThreadSerializer(serializers.ModelSerializer):
                
    content = serializers.JSONField(required=True)
    media = serializers.ListField(
        required=False, child=MediaFileSerializer(
            required=False))

    def create(self, validated_data):
        data = validated_data.copy()
        if "media" in data:
            del data["media"]
 
        mask = self.context["mask"]
        obj = super().create({"mask": mask, **data})
        media_data = validated_data.get("media", [])
        tags = get_tags_list(data["text"])

        create_tags(obj, tags)
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
                        order_by(
                            '-reaction_count')
        
        last_reaction = ReactionRelation.objects.filter(
            thread=instance,
            is_active=True, 
            mask=self.context["mask"]
        )

        representation["parent"] = head_id
        representation["responses_count"] = subs.count()
        representation["short_id"] = str(instance.id)[0:5]
        representation["is_expired"] = (now_date > instance.expire_date if \
            instance.expire_date else None)
        representation["media"] = ThreadMediaSerializer(
            media, many=True).data

        if self.context.get("show_responses"):
            representation["responses"] = ThreadSerializer(
                subs,
                many=True,
                context=({
                    "mask": self.context["mask"],
                    "short": True})).data

        last_reaction = last_reaction[0].reaction if last_reaction else None
        representation["last_reaction"] = ReactionSerializer(last_reaction, many=False).data if \
            last_reaction else None
        
        representation["reactions"] = ReactionSerializer(
                thread_reactions, many=True, context=({
                    "thread": instance})).data
        
        mask_data = MaskSerializer(instance.mask, many=False).data if \
            instance.mask else None
            
        representation["mask"] = mask_data
        representation["is_new"] = instance.is_new()
        representation["create_at"] = humanize.naturaldelta(
            timezone.now() - instance.create_at)

        return representation


    class Meta:
        model = Thread
        exclude = ("is_active", "update_at")
