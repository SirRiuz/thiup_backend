# Python
import hashlib


def get_user(request) -> (str):
    client = request.META.get('REMOTE_ADDR')
    hash = hashlib.sha256(client.encode()).hexdigest()
    return f"0x{hash[::6]}"
