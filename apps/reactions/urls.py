# Django
from django.urls import path, include
from rest_framework import routers

# Views
from apps.reactions.views.reaction_view import ReactionsViewSet


router = routers.DefaultRouter()
router.register(r"reactions", ReactionsViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
