# Python
import time
from datetime import datetime, timedelta

# Django
from django.utils import timezone
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.db.models import Count
from django.db.models.query import QuerySet
from rest_framework.throttling import AnonRateThrottle
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.status import *

# Models
from apps.threads.models.thread import Thread

# Serailizers
from apps.threads.serializers.thread_serializer import \
    ThreadSerializer

# Libs
from apps.default.permissions.client import IsClientAuthenticated
from apps.threads.pagination.thread_pagination import CustomThreadPagination
from apps.threads.methods.threads import get_ranked_thread
from apps.reactions.models.reaction_relation import ReactionRelation, Reaction
from apps.reactions.serializers.reaction_serializer import (
    ReactionCountSerializer,
    BaseReactionSerializer
)


class ThreadsViewSet(GenericViewSet):

    queryset = Thread.objects.filter(is_active=True, visibility=True)
    pagination_class = CustomThreadPagination
    serializer_class = ThreadSerializer
    permission_classes = (IsClientAuthenticated, )

    def get_queryset(self) -> (QuerySet):
        queryset = super().get_queryset()
        now_date = timezone.localtime(timezone.now())

        if self.action == "retrieve" or self.action == "responses":
            thread_id = self.kwargs.get("pk")
            short_id_size = 5
            thread = Thread.objects.filter(
                Q(expire_date__gte=now_date)
                | Q(expire_date__isnull=True),
                is_active=True)

            if thread_id and len(thread_id) == short_id_size:
                return thread.filter(id__startswith=thread_id)

            return thread.filter(id=thread_id)

        if self.action == "list":
            query = self.request.GET.get("q")
            tag = self.request.GET.get("tag")
            threads = get_ranked_thread().filter(
                Q(expire_date__gte=now_date)
                | Q(expire_date__isnull=True),
                visibility=True,
                is_active=True,
                sub__isnull=True)

            if tag:
                return threads.filter(
                    tag__name=tag.lower()).order_by("-index")

            if query:
                return threads.filter(
                    text__icontains=query).order_by("-index")

            return threads.order_by("-index")

        return queryset

    def list(self, request) -> (Response):
        """
        Gets a paged list of threads
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        Request params:

                tag - Winter the threads by hashtag
                query - Winter the threads by query param
        ---
        response code: 200
        ---
        Response Body:

                {
                    "count": 0,
                    "next": "",
                    "previous": "",
                    "results": [
                        {
                            "id": "...",
                            "create_at": "4 days",
                            "content": "...",
                            "text": "...",
                            "visibility": true,
                            "expire_date": null,
                            "sub": null,
                            "mask": {
                                "id": "...",
                                "hash": "...",
                                "miniature": "..."
                            },
                            "parent": null,
                            "responses_count": 3,
                            "short_id": "3c96a",
                            "is_expired": null,
                            "media": [],
                            "last_reaction": null,
                            "reactions": [
                                {
                                    "id": "...",
                                    "name": "...",
                                    "icon": "...",
                                    "reaction_count": 1
                                }
                            ],
                            "is_new": false
                        },
                        ...
                    ]
                }

        Response codes:

            201 - Obtains a list of threads.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        pages = self.paginate_queryset(self.get_queryset())
        serializer = self.get_serializer(
            pages,
            many=True,
            context=({"mask": request.mask, "short": True}))
                
        return self.get_paginated_response(({
            "data": serializer.data}))

    def retrieve(self, request, pk) -> (Response):
        """
        Retrieve the infromation of the thread
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

                {
                    "id": "...",
                    "create_at": "4 days",
                    "content": "...",
                    "text": "...",
                    "visibility": true,
                    "expire_date": null,
                    "sub": null,
                    "mask": {
                        "id": "...",
                        "hash": "...",
                        "miniature": "..."
                    },
                    "parent": null,
                    "responses_count": 3,
                    "short_id": "3c96a",
                    "is_expired": null,
                    "media": [],
                    "last_reaction": null,
                    "reactions": [
                        {
                            "id": "...",
                            "name": "s",
                            "icon": "...",
                            "reaction_count": 1
                        }
                    ],
                    "is_new": false
                }

        Response codes:

            201 - Obtains an object of a thread.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        thread = get_object_or_404(self.get_queryset())
        serializer = self.get_serializer(
            thread, many=False, context=({
                "mask": request.mask})).data

        return Response(serializer, status=HTTP_200_OK)

    def create(self, request) -> (Response):
        """
        Create a new thread
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 200
        ---
        Request body:

                {
                    "media": [
                        ...
                    ],
                    "content": {
                        ...
                    },
                    "text": "...",
                    "visibility": false,
                    "expire_date": null,
                    "sub": null,
                    "mask": null
                }
        
        Response Body:

                {
                    "id": "...",
                    "create_at": "a moment",
                    "content": {
                        ...
                    },
                    "text": "kasndads",
                    "visibility": false,
                    "expire_date": null,
                    "sub": null,
                    "mask": null,
                    "parent": null,
                    "responses_count": 0,
                    "short_id": "ed20a",
                    "is_expired": null,
                    "media": [],
                    "last_reaction": null,
                    "reactions": [],
                    "responses": [],
                    "is_new": true
                }

        Response codes:

            201 - Obtains an object of the created thread.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        sub_tread = request.data.get("sub")
        text = request.data.get("text")
        if sub_tread:
            sub_tread = get_object_or_404(
                Thread.objects.filter(id=sub_tread)).id

        serializer = self.get_serializer(data=request.data, context=({
            "mask": request.mask,
            "show_responses": True
        }))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=HTTP_201_CREATED)

    @action(detail=True, methods=["GET"])
    def responses(self, request, pk) -> (Response):
        """
        Get responses of the thread
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

                {
                    "head": {
                        "id": "...",
                        "content": "...",
                        "create_at": "11 days",
                        "text": "...",
                        "visibility": true,
                        "expire_date": null,
                        "sub": null,
                        "mask": {
                            "id": "...",
                            "is_active": true,
                            "hash": "...",
                            "country_code": "Unknow",
                            "miniature": null
                        },
                        "parent": null,
                        "responses_count": 13,
                        "short_id": "4c9a8",
                        "is_expired": null,
                        "media": [],
                        "last_reaction": null,
                        "reactions": [],
                        "is_new": false
                    },
                    "count": 13,
                    "next": null,
                    "previous": "...",
                    "results": [
                        {
                            "id": "...",
                            "content": "...",
                            "create_at": "10 days",
                            "text": "low",
                            "visibility": true,
                            "expire_date": null,
                            "sub": "...",
                            "mask": {
                                "id": "...",
                                "is_active": true,
                                "hash": "...",
                                "country_code": "Unknow",
                                "miniature": null
                            },
                            "parent": "...",
                            "responses_count": 0,
                            "short_id": "...",
                            "is_expired": null,
                            "media": [],
                            "responses": [],
                            "last_reaction": null,
                            "reactions": [],
                            "is_new": false
                        },
                        ...
                    ]
                }

        Response codes:

            200 - Returns a list with the responses of a thread.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        thread = get_object_or_404(self.get_queryset())
        head_serializer = self.get_serializer(
            thread, many=False, context=({
                "mask": request.mask}))

        responses = get_ranked_thread().filter(
            is_active=True, sub=thread).order_by("-index")

        pages = self.paginate_queryset(responses)
        serializer = self.get_serializer(pages, many=True, context=({
            "mask": request.mask, "show_responses": True}))

        return self.get_paginated_response(({
            "data": serializer.data,
            "context": {"head": head_serializer.data}
        }))
