# Django
from django.urls import path, include
from rest_framework import routers

# Views
from apps.threads.views.thread_view import ThreadsViewSet

router = routers.DefaultRouter()
router.register(r"threads", ThreadsViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
