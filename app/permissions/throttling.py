# DRF throttles keyed on the TRUSTED client IP.
#
# DRF's default `get_ident` reads the whole X-Forwarded-For string (or
# REMOTE_ADDR) as the throttle key — spoofable, so a client that rotates the
# header gets a fresh bucket every request and defeats every per-IP limit.
# These subclasses key on `get_client_ip` (the hardened resolver) so throttles
# bind to the same unspoofable identity the rest of the app uses.
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

from app.utils.client import get_client_ip


class TrustedIPScopedRateThrottle(ScopedRateThrottle):
    def get_ident(self, request):
        return get_client_ip(request)


class TrustedIPAnonRateThrottle(AnonRateThrottle):
    def get_ident(self, request):
        return get_client_ip(request)
