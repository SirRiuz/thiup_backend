# Django
from django.conf import settings


def get_client_ip(request) -> str:
    """Resolve the REAL client IP, resistant to X-Forwarded-For spoofing.

    Everything downstream (mask identity, GeoIP, honeypot blacklist, per-IP
    throttles) keys on this value, so trusting the wrong hop lets a client
    forge its identity, evade throttles and poison the blacklist.

    Trust order:
      1. `CF-Connecting-IP` when `TRUST_CLOUDFLARE` is on. Cloudflare's edge
         SETS this to the true visitor and OVERWRITES any client-supplied
         value, so behind the tunnel it is the only unspoofable source (and
         it is simply absent on a forged/direct request → we fall through).
      2. The X-Forwarded-For entry the OUTERMOST trusted proxy appended.
         A reverse proxy (nginx `$proxy_add_x_forwarded_for`, Cloudflare)
         APPENDS the real peer, so the client can only forge entries to the
         LEFT of it — taking the Nth-from-the-right entry (N =
         `TRUSTED_PROXY_COUNT`) ignores anything the client prepended. This
         is why the previous `split(",")[0]` (leftmost) was spoofable.
      3. `REMOTE_ADDR` (no proxy in front).
    """
    if settings.TRUST_CLOUDFLARE:
        cf_ip = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        if cf_ip:
            return cf_ip

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            index = min(settings.TRUSTED_PROXY_COUNT, len(parts))
            return parts[-index]

    return request.META.get("REMOTE_ADDR", "")


# Backwards-compatible alias: the old name is still imported in a couple of
# places (and tests). Both resolve through the hardened logic above.
def get_client_addres(request) -> str:
    return get_client_ip(request)
