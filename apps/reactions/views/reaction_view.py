# Django
from django.shortcuts import get_object_or_404
from django.db.models import Count
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.status import *

# Libs
from django_ratelimit.decorators import ratelimit
from apps.threads.models.thread import Thread
from apps.reactions.models.reaction_relation import (
    ReactionRelation,
    Reaction
)
from apps.default.permissions.client import IsClientAuthenticated
from apps.reactions.serializers.reaction_serializer import (
    ReactionRelationSerializer,
    ReactionShortSerializer,
    ReactionCountSerializer,
    BaseReactionSerializer,
    ReactionSerializer
)


class ReactionsViewSet(GenericViewSet):
    
    queryset = ReactionRelation.objects.filter(is_active=True)
    serializer_class = ReactionRelationSerializer
    permission_classes = (IsClientAuthenticated, )
    
    def list(self, request) -> (Response):
        """
        Retrieve a reaction list
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 200
        ---
        Response Body:

                [
                    {
                        "id": "",
                        "name": "",
                        "icon": ""
                    },
                    ...
                ]
        Response codes:

            201 - Obtains a list of reaction objects.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        queryset = Reaction.objects.filter(is_active=True)
        serializer = BaseReactionSerializer(queryset, many=True)
        return Response(serializer.data, status=HTTP_200_OK)

    def create(self, request) -> (Response):
        """
        Create a new reaction for a thread
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 200
        ---
        Request Body:

                {
                    "reaction": "...",
                    "thread": "..."
                }

        Response Body:

                {
                    "my_reaction": {
                        "reaction": "",
                        "thread": ""
                    },
                    "reactions": [
                        {
                            "id": "",
                            "name": "",
                            "icon": "...",
                            "reaction_count": 1
                        }
                    ]
                }

        Response codes:

            201 - Obtains an object of the created reaction.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.create({**serializer.data, "mask": request.mask})
        thread_reactions = Reaction.objects.filter(
            is_active=True,
            reactionrelation__thread=request.data["thread"]).\
                annotate(reaction_count=Count(
                    'reactionrelation')).\
                        order_by(
                            '-reaction_count')

        my_reaction = ReactionRelationSerializer(data)
        serializer = ReactionSerializer(thread_reactions, context=({
                    "thread": request.data["thread"]}), many=True)

        return Response({
            "my_reaction": (my_reaction.data if\
                data else None),
                
            "reactions": serializer.data,
            }, status=HTTP_201_CREATED)
