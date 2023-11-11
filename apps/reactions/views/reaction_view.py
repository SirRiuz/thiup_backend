# Django
from django.shortcuts import get_object_or_404
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Count
from rest_framework.status import *

# Libs
from apps.threads.models.thread import Thread
from apps.default.methods.user import get_user
from apps.reactions.models.reaction_relation import (
    ReactionRelation,
    Reaction
)
from apps.reactions.serializers.reaction_serializer import (
    ReactionRelationSerializer,
    ReactionShortSerializer,
    ReactionCountSerializer,
    BaseReactionSerializer
)


class ReactionsViewSet(GenericViewSet):
    serializer_class = ReactionRelationSerializer
    queryset = ReactionRelation.objects.filter(
        is_active=True)
    
    def get_queryset(self):
        if self.action == "list":
            return Reaction.objects.filter(
                is_active=True)

        if self.request.method.lower() == "delete":
            user = get_user(self.request)
            thread = Thread.objects.get(
                id=self.request.data["thread"])
            
            return ReactionRelation.objects.filter(
                thread=thread,
                user=user,
                is_active=True)
            
        return super().get_queryset()
    
    def list(self, request) -> (Response):
        serializer = BaseReactionSerializer(self.get_queryset(), many=True)
        return Response(serializer.data, status=HTTP_200_OK)

    def delete(self, request) -> (Response):
        serializer = self.get_serializer(data=request.data, many=False)
        serializer.is_valid(raise_exception=True)
        query = get_object_or_404(self.get_queryset())
        query.delete()
        return Response("delete", status=HTTP_200_OK)

    def create(self, request) -> (Response):
        user = get_user(request)
        serializer = self.get_serializer(data=request.data, many=False)
        serializer.is_valid(raise_exception=True)
        data = serializer.create({
            **serializer.data,
            "user": user})

        return Response(status=HTTP_201_CREATED)
    
    @action(detail=True, methods=['GET'])
    def thread(self, request, pk) -> (Response):
        user = get_user(request)
        queryset = Thread.objects.filter(is_active=True, id=pk)
        thread = get_object_or_404(queryset)
        reactions_relations = ReactionRelation.objects.filter(
            is_active=True, thread=thread).values_list(
                "reaction", flat=True).distinct()    
        reactions = Reaction.objects.filter(
            is_active=True, id__in=reactions_relations).annotate(
                reaction_count=Count(
                    "reactionrelation"))\
                        .order_by("-reaction_count")
                   
        my_reaction = ReactionRelation.objects.filter(
            is_active=True, user=user)
        my_reaction = my_reaction.first().reaction if \
            my_reaction.exists() else None
            
        serializer = ReactionCountSerializer(reactions, many=True)
        my_reaction_s = BaseReactionSerializer(my_reaction, many=False)

        return Response({
            "my_reaction": (my_reaction_s.data if\
                my_reaction else None),
            "reactions": serializer.data
        }, status=HTTP_200_OK)
