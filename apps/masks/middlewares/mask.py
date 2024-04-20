# Python
import hashlib

# Models
from apps.masks.models.mask_model import Mask
from apps.masks.models.miniature_model import Miniature

# Libs
import ipaddress
from apps.masks.methods.location import get_country


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
