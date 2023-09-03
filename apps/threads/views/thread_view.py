import time

# Django
from django.utils import timezone
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.status import *

# Models
from apps.threads.models.thread import Thread

# Serailizers
from apps.threads.serializers.thread_serializer import \
    ThreadSerializer
    
# Libs
from apps.threads.pagination.thread_pagination import \
    CustomThreadPagination


class ThreadsViewSet(GenericViewSet):
    pagination_class = CustomThreadPagination
    serializer_class = ThreadSerializer
    queryset = Thread.objects.filter(
        is_active=True, visibility=True)
    
    def get_queryset(self):
        queryset = super().get_queryset()
        now_date = timezone.localtime(timezone.now())
        
        if self.action == 'retrieve' or self.action == 'responses':
            thread_id = self.kwargs.get("pk")
            short_id_size = 5
            
            if len(thread_id) == short_id_size:
                return Thread.objects.filter(
                    Q(expire_date__gte=now_date) \
                        | Q(expire_date__isnull=True),
                    is_active=True,
                    id__startswith=thread_id)
            
            return Thread.objects.filter(
                Q(expire_date__gte=now_date) \
                    | Q(expire_date__isnull=True),
                is_active=True,
                id=thread_id)
        
        if self.action == 'list':
            query = self.request.GET.get("q")
            if query:
                return Thread.objects.filter(
                    Q(expire_date__gte=now_date) \
                        | Q(expire_date__isnull=True),
                    visibility=True,
                    is_active=True,
                    sub__isnull=True,
                    text__icontains=query)
            
            return Thread.objects.filter(
                Q(expire_date__gte=now_date) \
                    | Q(expire_date__isnull=True),
                visibility=True,
                is_active=True,
                sub__isnull=True)
        
        return queryset
    
    def list(self, request):
        pages = self.paginate_queryset(self.get_queryset())
        serializer = self.get_serializer(
            pages,
            many=True,
            context=({"short": True}))
        
        return self.get_paginated_response(({
            "data": serializer.data}))

    def retrieve(self, request, pk):
        thread = get_object_or_404(self.get_queryset())
        serializer = self.get_serializer(thread, many=False).data
        return Response(serializer, status=HTTP_200_OK)

    def create(self, request):
        sub_tread = request.data.get("sub")
        text = request.data.get("text")
        if sub_tread:
            sub_tread = get_object_or_404(
                Thread.objects.filter(id=sub_tread)).id
        
        serializer = self.get_serializer(data=request.data)
        
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=HTTP_200_OK)

    @action(detail=True, methods=['GET'])
    def responses(self, request, pk):
        thread = get_object_or_404(self.get_queryset())
        head_serializer = self.get_serializer(thread, many=False)
        responses = Thread.objects.filter(is_active=True, sub=thread)
        pages = self.paginate_queryset(responses)
        serializer = self.get_serializer(pages, many=True)
        return self.get_paginated_response(({
            "data": serializer.data,
            "context": ({
                "head": head_serializer.data
            })
        }))
