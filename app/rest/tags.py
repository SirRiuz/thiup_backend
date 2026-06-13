# Django
from rest_framework.viewsets import GenericViewSet
from rest_framework.status import *

# Libs
from app.models.tag import Tag
from app.rest.serializers.tag_serializer import TagSerializer
from app.permissions.client import IsClientAuthenticated


class TagsViewSet(GenericViewSet):

    serializer_class = TagSerializer
    queryset = Tag.objects.filter(is_active=True)
    permission_classes = (IsClientAuthenticated, )

