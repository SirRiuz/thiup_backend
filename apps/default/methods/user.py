# Python
import hashlib


def get_user(request) -> (str):
    client = request.META.get('HTTP_X_FORWARDED_FOR')
    if client:
        client = client.split(',')[0]
    else:
        client = request.META.get('REMOTE_ADDR')

    hash = hashlib.sha256(client.encode()).hexdigest()
    return f"0x{hash[::6]}"
