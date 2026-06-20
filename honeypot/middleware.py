# Django
from django.utils.deprecation import MiddlewareMixin
from django.http import HttpResponseForbidden

# Libs
from app.utils.client import get_client_addres
from honeypot.models.black_list import BlackList


class HoneyPotMiddleware(MiddlewareMixin):
    def process_request(self, request):
        assert hasattr(request, "session"), (
            "The Django authentication middleware requires session middleware "
            "to be installed. Edit your MIDDLEWARE_CLASSES setting to insert "
            "'django.contrib.sessions.middleware.SessionMiddleware' before "
            "'django.contrib.auth.middleware.AuthenticationMiddleware'."
        )

        # The healthcheck must respond even if the DB is down: the BlackList
        # query would blow up here (raw 500) before the view could return
        # its controlled 503.
        if request.path == "/health/":
            return None

        self.client_ip = get_client_addres(request)
        if (
            BlackList.objects.filter(ip_address=self.client_ip).exists()
            and not request.user.is_staff
        ):
            return HttpResponseForbidden(
                "You are not allowed to call the website anymore."
            )
