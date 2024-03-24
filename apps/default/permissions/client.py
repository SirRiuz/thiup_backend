# Python
import hashlib
import json

# Django
from rest_framework.permissions import BasePermission
from django.conf import settings

# Libs
from jwt.exceptions import PyJWTError
from apps.default.methods.tokens import decode_token
from apps.default.methods.storage import save_token, check_token


class IsClientAuthenticated(BasePermission):
    """
    Allows access only to users with the client token.
    """
    
    TOKEN_TYPE = "X-Dynamic-Token"

    def has_permission(self, request, view) -> (bool):
        client_token = request.headers.get(self.TOKEN_TYPE, "")

        if not settings.SINGLE_REQUEST_PROTECT:
            return True

        try:
            decode_token(client_token)
            if not check_token(client_token):
                save_token(client_token)
                return True

        except PyJWTError:
            pass
