# Django
from rest_framework import serializers
from django.db.models import Count

# Models
from apps.reactions.models.reaction_relation import ReactionRelation
from apps.reactions.models.reaction import Reaction
from apps.threads.models.thread import Thread


class ReactionSerializer(serializers.ModelSerializer):
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if "thread" in self.context:
            thread = self.context["thread"]
            reaction_count = ReactionRelation.objects.filter(
                is_active=True,
                thread=thread,
                reaction=instance).count()
            representation["reaction_count"] = reaction_count
            
        return representation
    
    class Meta:
        model = Reaction
        fields = (
            "id",
            "name",
            "icon")
        
class ReactionShortSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        thread_reactions = Reaction.objects.filter(
            is_active=True,
            reactionrelation__thread=instance.thread).\
                annotate(reaction_count=Count(
                    'reactionrelation')).\
                        order_by('-reaction_count', 'id')

        representation["thread_reactions"] = ReactionSerializer(
            thread_reactions,
            many=True,
            context=({"thread": instance.thread})).data
        
        return representation
    
    class Meta:
        model = ReactionRelation
        fields = (
            "user",
            "id",
            "thread",
            "reaction")

class ReactionRelationSerializer(serializers.Serializer):
    reaction = serializers.PrimaryKeyRelatedField(
        required=False,
        queryset=Reaction.objects.filter(is_active=True))
    thread = serializers.PrimaryKeyRelatedField(
        queryset=Thread.objects.filter(is_active=True))
    
    def create(self, validated_data):
        reaction = Reaction.objects.get(
            id=validated_data["reaction"])
        thread = Thread.objects.get(
            id=validated_data["thread"])

        hash_last_reaction = ReactionRelation.objects.filter(
            thread=thread,
            user=validated_data["user"])
        
        if hash_last_reaction.exists():
            hash_last_reaction.delete()
            
        return ReactionRelation.objects.create(
            reaction=reaction,
            thread=thread,
            user=validated_data["user"])
        
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        return representation
        
    class Meta:
        model = ReactionRelation
        exclude = ("is_active", "user")
