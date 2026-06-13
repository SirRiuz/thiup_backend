# Python
import hashlib

# Models
from app.models.mask import Mask
from app.models.miniature import Miniature

# Libs
import ipaddress
from app.methods.location import get_country


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

        if True:
        # if not request.user.is_superuser:
            miniature = Miniature.objects.filter(
                is_active=True).order_by("?")

            obj, is_created = Mask.objects.get_or_create(hash=hash)

            if obj.country_code != country:
                obj.country_code = country
                obj.save()

            if (is_created and miniature) or (not obj.miniature and miniature):
                miniature = miniature[0]
                obj.miniature = miniature
                obj.save()

            request.mask = obj

        response = self.get_response(request)
        return response
