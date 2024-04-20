# Django
from django.urls import path, include
from rest_framework import routers

# Views
from apps.tags.views.tag_viewset import TagsViewSet

router = routers.DefaultRouter()
router.register(r"tags", TagsViewSet)


urlpatterns = [
    path("", include(router.urls)),
]
