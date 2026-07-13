# Django
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin

# Libs
from app.utils.client import get_client_addres
from honeypot.app_settings import BLACKLIST_CACHE_KEY, BLACKLIST_CACHE_TTL
from honeypot.models.black_list import BlackList


def blacklisted_ips() -> frozenset:
    """The full blacklisted-IP set, cached in LocMem.

    One tiny SELECT on cache miss instead of an exists() round trip on every
    request. Writes/deletes on BlackList invalidate the key via signals, so
    enforcement stays immediate.
    """
    ips = cache.get(BLACKLIST_CACHE_KEY)
    if ips is None:
        ips = frozenset(BlackList.objects.values_list("ip_address", flat=True))
        cache.set(BLACKLIST_CACHE_KEY, ips, BLACKLIST_CACHE_TTL)
    return ips


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

        # Local variable on purpose: the middleware instance is shared across
        # gthread threads, so storing the IP on self was a latent race.
        client_ip = get_client_addres(request)
        if client_ip in blacklisted_ips() and not request.user.is_staff:
            return HttpResponseForbidden("You are not allowed to call the website anymore.")
