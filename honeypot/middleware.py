# Django
from django.utils.deprecation import MiddlewareMixin
from django.http import HttpResponseForbidden

# Libs
from apps.default.utils.client import get_client_addres
from honeypot.models.black_list import BlackList


class HoneyPotMiddleware(MiddlewareMixin):
    def process_request(self, request):
        assert hasattr(request, "session"), (
            "The Django authentication middleware requires session middleware "
            "to be installed. Edit your MIDDLEWARE_CLASSES setting to insert "
            "'django.contrib.sessions.middleware.SessionMiddleware' before "
            "'django.contrib.auth.middleware.AuthenticationMiddleware'."
        )

        self.client_ip = get_client_addres(request)
        if (
            BlackList.objects.filter(ip_address=self.client_ip).exists()
            and not request.user.is_staff
        ):
            return HttpResponseForbidden(
                "You are not allowed to call the website anymore."
            )
