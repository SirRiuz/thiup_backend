# Python
import hashlib

# Libs
from app.methods.location import get_country
from app.methods.presence import mark_online

# Models
from app.models.mask import Mask


class MaskMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __get_user(self, request) -> str:
        """Get client address"""
        client = request.META.get("HTTP_X_FORWARDED_FOR")
        if client:
            client = client.split(",")[0]
        else:
            client = request.META.get("REMOTE_ADDR")

        return client

    def __call__(self, request):
        # The healthcheck doesn't need a mask and must respond even if the DB
        # is down: without this exemption, the get_or_create below would blow up
        # in the middleware (raw 500) before reaching the view (controlled 503).
        # It also avoids writing masks on every health ping.
        if request.path == "/health/":
            request.mask = None
            return self.get_response(request)

        address = self.__get_user(request)
        hash = hashlib.sha256(address.encode()).hexdigest()
        country = get_country(address)
        request.mask = None

        obj, _ = Mask.objects.get_or_create(hash=hash)

        if obj.country_code != country:
            obj.country_code = country
            obj.save()

        request.mask = obj
        # Passive presence: every request the user makes IS the heartbeat.
        # Ephemeral cache-only marking (60 s TTL) — see app/methods/presence.
        mark_online(obj.hash)

        response = self.get_response(request)
        return response
