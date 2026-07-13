# Python
import hashlib

# Django
from django.core.cache import cache

# Libs
from app.methods.location import get_country
from app.methods.presence import mark_online

# Models
from app.models.mask import Mask
from app.utils.client import get_client_ip

# A Mask row is immutable except for country_code, so a short-lived cached
# copy is safe and saves the get_or_create SELECT before EVERY view — with a
# remote DB that round trip is pure latency. LocMem (per-process), same TTL
# as the presence window; a country change still writes through immediately.
MASK_CACHE_TTL = 60


class MaskMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # The healthcheck doesn't need a mask and must respond even if the DB
        # is down: without this exemption, the get_or_create below would blow up
        # in the middleware (raw 500) before reaching the view (controlled 503).
        # It also avoids writing masks on every health ping.
        if request.path == "/health/":
            request.mask = None
            return self.get_response(request)

        address = get_client_ip(request)
        hash = hashlib.sha256(address.encode()).hexdigest()
        country = get_country(address)
        request.mask = None

        cache_key = f"mask:{hash}"
        obj = cache.get(cache_key)
        if obj is None:
            obj, _ = Mask.objects.get_or_create(hash=hash)

        if obj.country_code != country:
            obj.country_code = country
            # update_fields: only the changed column (plus auto_now) instead
            # of rewriting the whole row.
            obj.save(update_fields=["country_code", "update_at"])

        cache.set(cache_key, obj, MASK_CACHE_TTL)
        request.mask = obj
        # Passive presence: every request the user makes IS the heartbeat.
        # Ephemeral cache-only marking (60 s TTL) — see app/methods/presence.
        mark_online(obj.hash)

        response = self.get_response(request)
        return response
