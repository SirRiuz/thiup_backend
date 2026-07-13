from django.conf import settings

HONEYPOT_LOGIN_TRYOUT = getattr(settings, "HONEYPOT_LOGIN_TRYOUT", 5)

# Cache key/TTL for the blacklisted-IP set (LocMem). The BlackList table is a
# tiny forensics table but its per-request exists() was a DB round trip before
# EVERY view; signals on BlackList invalidate this key so a fresh blacklisting
# takes effect immediately.
BLACKLIST_CACHE_KEY = "honeypot:blacklist_ips"
BLACKLIST_CACHE_TTL = 60
