# Python
import time

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
from apps.threads.pagination.thread_pagination import CustomThreadPagination
from apps.reactions.models.reaction_relation import (
    ReactionRelation,
    Reaction
)
from apps.reactions.serializers.reaction_serializer import (
    ReactionCountSerializer,
    BaseReactionSerializer
)


class ThreadsViewSet(GenericViewSet):

    pagination_class = CustomThreadPagination
    serializer_class = ThreadSerializer
    queryset = Thread.objects.filter(
        is_active=True, visibility=True)

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
            threads = Thread.objects.filter(
                Q(expire_date__gte=now_date)
                | Q(expire_date__isnull=True),
                visibility=True,
                is_active=True,
                sub__isnull=True)

            if tag:
                return threads.filter(tag__name=tag.lower())

            if query:
                return threads.filter(
                    text__icontains=query)

            return threads

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
        response body:

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
        """
        pages = self.paginate_queryset(self.get_queryset())
        serializer = self.get_serializer(
            pages,
            many=True,
            context=({
                "mask": request.mask,
                "short": True}))
                
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
        response body:

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
        """
        thread = get_object_or_404(self.get_queryset())
        serializer = self.get_serializer(
            thread, many=False, context=({
                "mask": request.mask})).data

        return Response(serializer, status=HTTP_200_OK)

    def create(self, request):
        """
        Create a new thread
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
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
        ---
        response code: 200
        ---
        response body:

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
        """
        thread = get_object_or_404(self.get_queryset())
        head_serializer = self.get_serializer(
            thread, many=False, context=({
                "mask": request.mask
                }))

        responses = Thread.objects.filter(is_active=True, sub=thread)
        pages = self.paginate_queryset(responses)
        serializer = self.get_serializer(pages, many=True, context=({
            "mask": request.mask,
            "show_responses": True
            }))

        return self.get_paginated_response(({
            "data": serializer.data,
            "context": ({
                "head": head_serializer.data
            })
        }))

    @action(detail=True, methods=['GET'])
    def stats(self, request, pk) -> (Response):
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
            is_active=True, 
            mask=request.mask
        )
        my_reaction = my_reaction.first().reaction if \
            my_reaction.exists() else None
            
        serializer = ReactionCountSerializer(reactions, many=True)
        my_reaction_s = BaseReactionSerializer(my_reaction, many=False)

        return Response({
            "my_reaction": (my_reaction_s.data if\
                my_reaction else None),
            "reactions": serializer.data,
            "views": "..."
            }, status=HTTP_200_OK)
