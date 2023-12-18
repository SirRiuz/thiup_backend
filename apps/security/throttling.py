# Django
from rest_framework.throttling import AnonRateThrottle


class CustomAnonRateThrottle(AnonRateThrottle):

    scope = "anon"

    def __init__(self, allow_all=None, *args, **kwargs):
        self.allow_all = allow_all
        super().__init__(*args, **kwargs)

    def get_cache_key(self, request, view=None):
        if request.user and request.user.is_authenticated:
            return None

        return self.cache_format % ({
            "scope": self.scope,
            "ident": self.get_ident(request),
        })

    def allow_request(self, request, view=None):
        if self.allow_all:
            return True

        return super().allow_request(request, view)


    def allow(self, request):
        key = self.get_cache_key(request)
        print("Allow the user")
        print(key)
        print()
        self.cache.delete(key)
