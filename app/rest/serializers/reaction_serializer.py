# Django
from rest_framework import serializers

from app.models.reaction import Reaction

# Models
from app.models.reaction_relation import ReactionRelation
from app.models.thread import Thread


class BaseReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reaction
        fields = ("id", "name", "emoji")


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


class ReactionRelationSerializer(serializers.Serializer):
    reaction = serializers.PrimaryKeyRelatedField(queryset=Reaction.objects.filter(is_active=True))

    # The public identifier of a thread is its `uid` (not the UUID pk), same
    # as ThreadSerializer.sub. The frontend only knows the uid (the UUID is
    # excluded from the ThreadSerializer), so the reaction is addressed by uid.
    thread = serializers.SlugRelatedField(slug_field="uid", queryset=Thread.objects.filter(is_active=True))

    def create(self, validated_data) -> ReactionRelation:
        # validated_data carries the INSTANCES already resolved by the related
        # fields (PrimaryKeyRelatedField / SlugRelatedField) — re-fetching them
        # by id/uid here cost two redundant queries per react.
        reaction = validated_data["reaction"]
        thread = validated_data["thread"]
        hash_last_reaction = (
            ReactionRelation.objects.filter(mask=validated_data["mask"], thread=thread)
            .select_related("reaction")
            .first()
        )

        if hash_last_reaction:
            last_reaction = hash_last_reaction.reaction
            hash_last_reaction.delete()
            if last_reaction == reaction:
                return

        return ReactionRelation.objects.create(mask=validated_data["mask"], reaction=reaction, thread=thread)

    class Meta:
        model = ReactionRelation
        exclude = ("is_active", "user")
