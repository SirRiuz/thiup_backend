# Django
from django.urls import path, re_path

# Libs
from honeypot.views.login_views import *

app_name = "honeypot"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    re_path("^", RedirectToLogin.as_view()),
]
