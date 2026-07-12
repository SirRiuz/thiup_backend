# Django
from django.db.models import Count
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED

# Libs
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.viewsets import GenericViewSet

from app.models.reaction_relation import Reaction, ReactionRelation
from app.models.thread import Thread
from app.permissions.captcha import human_validator
from app.permissions.client import IsClientAuthenticated
from app.rest.serializers.reaction_serializer import (
    BaseReactionSerializer,
    ReactionRelationSerializer,
    ReactionSerializer,
)


class ReactionsViewSet(GenericViewSet):
    queryset = ReactionRelation.objects.filter(is_active=True)
    serializer_class = ReactionRelationSerializer
    permission_classes = (IsClientAuthenticated,)

    def get_throttles(self) -> list:
        # Rate-limit ONLY reacting (anti-abuse companion of the captcha
        # human pass): the catalog list stays unthrottled.
        if self.action == "create":
            self.throttle_scope = "reactions_create"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def list(self, request) -> Response:
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
                        "emoji": ""
                    },
                    ...
                ]
        Response codes:

            201 - Obtains a list of reaction objects.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        queryset = Reaction.objects.filter(is_active=True)
        serializer = BaseReactionSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data, status=HTTP_200_OK)

    @human_validator
    def create(self, request) -> Response:
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
                            "emoji": "...",
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
        # `thread` in the payload is the public uid; we resolve the instance for
        # the queries (the FKs are by UUID pk, they don't accept the uid as string).
        thread = Thread.objects.get(uid=request.data["thread"])
        thread_reactions = (
            Reaction.objects.filter(is_active=True, reactionrelation__thread=thread)
            .annotate(reaction_count=Count("reactionrelation"))
            .order_by("-reaction_count")
        )

        my_reaction = ReactionRelationSerializer(data, context={"request": request})
        serializer = ReactionSerializer(thread_reactions, context={"thread": thread, "request": request}, many=True)

        return Response(
            {
                "my_reaction": (my_reaction.data if data else None),
                "reactions": serializer.data,
            },
            status=HTTP_201_CREATED,
        )
