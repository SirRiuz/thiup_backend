# Django
from rest_framework.status import *
from rest_framework.viewsets import GenericViewSet

# Libs
from app.models.tag import Tag
from app.permissions.client import IsClientAuthenticated
from app.rest.serializers.tag_serializer import TagSerializer


class TagsViewSet(GenericViewSet):
    serializer_class = TagSerializer
    queryset = Tag.objects.filter(is_active=True)
    permission_classes = (IsClientAuthenticated,)
