# Django
from django.shortcuts import get_object_or_404
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.status import *

# Libs
from apps.threads.models.thread import Thread
from apps.default.methods.user import get_user
from apps.reactions.models.reaction_relation import ReactionRelation
from apps.reactions.serializers.reaction_serializer import (
    ReactionRelationSerializer,
    ReactionShortSerializer
)


class ReactionsViewSet(GenericViewSet):
    
    serializer_class = ReactionRelationSerializer
    queryset = ReactionRelation.objects.filter(
        is_active=True)
    
    def get_queryset(self):
        if self.request.method.lower() == "delete":
            print("delete")
            user = get_user(self.request)
            thread = Thread.objects.get(
                id=self.request.data["thread"])
            
            return ReactionRelation.objects.filter(
                thread=thread,
                user=user,
                is_active=True)
            
        return super().get_queryset()
    
    def list(self, request) -> (Response):
        serializer = self.get_serializer(self.get_queryset(), many=True)
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
        data = ReactionShortSerializer(data, many=False)
        return Response(data.data, status=HTTP_201_CREATED)
