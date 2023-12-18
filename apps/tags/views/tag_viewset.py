# Django
from django.shortcuts import get_object_or_404
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Count
from rest_framework.status import *

# Libs
from apps.tags.models.tag import Tag
from apps.tags.serializers.tag_serializer import TagSerializer


class TagsViewSet(GenericViewSet):

    queryset = Tag.objects.filter(is_active=True)
    serializer_class = TagSerializer

