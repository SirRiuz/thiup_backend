# Django
from django.shortcuts import get_object_or_404
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Count
from rest_framework.status import *

# Libs
from apps.threads.models.thread import Thread
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

    def create(self, request) -> (Response):
        serializer = self.get_serializer(data=request.data, many=False)
        serializer.is_valid(raise_exception=True)
        data = serializer.create({
            **serializer.data,
            "mask": request.mask
        })

        return Response(status=HTTP_201_CREATED)
