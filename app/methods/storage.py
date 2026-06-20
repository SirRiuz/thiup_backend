# Python
import hashlib

# Django
from django.core.cache import cache


def check_token(token) -> (bool):
    """
    Verifies if the token is already saved in the storage.
    """
    hash_token = hashlib.sha256(token.encode()).hexdigest()
    return cache.has_key(hash_token)


def save_token(token):
    """
    Saves the token hash in the storage to prevent
    future requests from being sent with the same token.
    """
    hash_token = hashlib.sha256(token.encode()).hexdigest()
    cache.set(hash_token, token)
