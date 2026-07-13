# Django
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED

# Libs
from rest_framework.viewsets import GenericViewSet

from app.models.reaction_relation import Reaction, ReactionRelation
from app.permissions.captcha import human_validator
from app.permissions.client import IsClientAuthenticated
from app.permissions.throttling import TrustedIPScopedRateThrottle
from app.rest.serializers.reaction_serializer import (
    BaseReactionSerializer,
    ReactionRelationSerializer,
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
            return [TrustedIPScopedRateThrottle()]
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
        React to a thread (toggle semantics: reacting with the same emoji
        again removes the reaction).
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 201
        ---
        Request Body:

                {
                    "reaction": "...",
                    "thread": "..."
                }

        Response Body:

                {"status": "ok"}

        The frontend renders reactions optimistically and never read the old
        echo of the thread's full reaction breakdown — recomputing it cost an
        aggregate plus one COUNT per reaction type on EVERY react, the most
        frequent write of the app. Minimal acknowledgement instead.

        Response codes:

            201 - The reaction was applied (or toggled off).
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # validated_data carries the resolved Reaction/Thread instances; the
        # serializer's create() reuses them (no re-fetch by id/uid).
        serializer.create({**serializer.validated_data, "mask": request.mask})

        return Response({"status": "ok"}, status=HTTP_201_CREATED)
