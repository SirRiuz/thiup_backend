def get_client_addres(request) -> str:
    """Best-effort client IP. Trusts X-Forwarded-For because nginx sets it."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
