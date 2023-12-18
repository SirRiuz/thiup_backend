# Django
from django.urls import path

# Views
from apps.security.views.robots_viewset import Robots


urlpatterns = (
    path("security/check_user/", Robots.as_view()),
)
