# Python
import hashlib

# Django
from django.core.cache import cache


def check_token(token) -> (bool):
    """
    Vetifica si el token ya esta guardado
    en la storage
    """
    hash_token = hashlib.sha256(token.encode()).hexdigest()
    return cache.has_key(hash_token)


def save_token(token):
    """
    Guarda el hash del token en la storage
    para evitar que se envien futuras peticiones
    con el mismo token.
    """
    hash_token = hashlib.sha256(token.encode()).hexdigest()
    cache.set(hash_token, token)
