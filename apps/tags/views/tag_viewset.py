# Django
from rest_framework.viewsets import GenericViewSet
from rest_framework.status import *

# Libs
from apps.tags.models.tag import Tag
from apps.tags.serializers.tag_serializer import TagSerializer
from apps.default.permissions.client import IsClientAuthenticated


class TagsViewSet(GenericViewSet):

    serializer_class = TagSerializer
    queryset = Tag.objects.filter(is_active=True)
    permission_classes = (IsClientAuthenticated, )

