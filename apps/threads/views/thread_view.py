# Django
from django.shortcuts import get_object_or_404
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.status import *

# Models
from apps.threads.models.thread import Thread

# Serailizers
from apps.threads.serializers.thread_serializer import ThreadSerializer


class ThreadsViewSet(GenericViewSet):

    queryset = Thread.objects.filter(is_active=True)
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        if self.action == 'retrieve':
            thread_id = self.kwargs.get("pk")
            return Thread.objects.filter(
                is_active=True,
                id=thread_id)
        
        if self.action == 'list':
            query = self.request.GET.get("q")
            if query:
                return Thread.objects.filter(
                    is_active=True,
                    text__icontains=query,
                    sub__isnull=True)
                
            return Thread.objects.filter(
                is_active=True,
                sub__isnull=True)
        
        return queryset

    def get_serializer_class(self):
        return ThreadSerializer

    def list(self, request):
        serializer = self.get_serializer(
            self.get_queryset(),
            many=True, context=(
                {"short": True}))
        
        return Response(serializer.data, status=HTTP_200_OK)

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
        return Response("Create a new thread", status=HTTP_200_OK)
