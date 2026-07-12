# Django
from django.db.models import Count
from rest_framework import serializers

from app.models.reaction import Reaction

# Models
from app.models.reaction_relation import ReactionRelation
from app.models.thread import Thread


class BaseReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reaction
        fields = ("id", "name", "emoji")


class ReactionCountSerializer(serializers.ModelSerializer):
    reaction_count = serializers.IntegerField(required=False)

    class Meta:
        model = Reaction
        fields = ("id", "name", "emoji", "reaction_count")


class ReactionSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if "thread" in self.context:
            thread = self.context["thread"]
            representation["reaction_count"] = ReactionRelation.objects.filter(
                is_active=True, thread=thread, reaction=instance
            ).count()

        return representation

    class Meta:
        model = Reaction
        fields = ("id", "name", "emoji")


class ReactionShortSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        thread_reactions = (
            Reaction.objects.filter(is_active=True, reactionrelation__thread=instance.thread)
            .annotate(reaction_count=Count("reactionrelation"))
            .order_by("-reaction_count", "id")
        )

        representation["thread_reactions"] = ReactionSerializer(
            thread_reactions, many=True, context=({"thread": instance.thread})
        ).data

        return representation

    class Meta:
        model = ReactionRelation
        fields = ("user", "id", "thread", "reaction")


class ReactionRelationSerializer(serializers.Serializer):
    reaction = serializers.PrimaryKeyRelatedField(queryset=Reaction.objects.filter(is_active=True))

    # The public identifier of a thread is its `uid` (not the UUID pk), same
    # as ThreadSerializer.sub. The frontend only knows the uid (the UUID is
    # excluded from the ThreadSerializer), so the reaction is addressed by uid.
    thread = serializers.SlugRelatedField(slug_field="uid", queryset=Thread.objects.filter(is_active=True))

    def create(self, validated_data) -> ReactionRelation:
        reaction = Reaction.objects.get(id=validated_data["reaction"])
        thread = Thread.objects.get(uid=validated_data["thread"])
        hash_last_reaction = ReactionRelation.objects.filter(mask=validated_data["mask"], thread=thread).first()

        if hash_last_reaction:
            last_reaction = hash_last_reaction.reaction
            hash_last_reaction.delete()
            if last_reaction == reaction:
                return

        return ReactionRelation.objects.create(mask=validated_data["mask"], reaction=reaction, thread=thread)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        return representation

    class Meta:
        model = ReactionRelation
        exclude = ("is_active", "user")
