# Django
from django.conf import settings

# Libs
import jwt


def encode_token(data) -> (str):
    """Create new jwt token"""
    return jwt.encode(
        data,
        settings.API_SECRET_KEY,
        algorithm="HS256"
    )


def decode_token(token) -> (dict):
    """Decode the client access token"""
    return jwt.decode(
        token,
        settings.API_SECRET_KEY,
        algorithms=["HS256"]
    )
