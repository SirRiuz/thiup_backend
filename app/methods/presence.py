# Python
import time

# Django
from django.core.cache import cache

# Ephemeral presence ("online now") tracking.
#
# LocMem-only by design: a mask counts as online while its cache key lives —
# expiry IS the offline transition, so no timestamps are stored, nothing is
# persisted and nothing is logged (privacy rule: no trackable history).
# Marking is PASSIVE: MaskMiddleware refreshes the key on every request the
# user makes while browsing, so no heartbeat endpoint is needed.
#
# LocMem is per-process: exact with the current single gunicorn worker; with
# more workers presence degrades gracefully (a user may show offline on the
# worker that hasn't seen them). Do NOT "fix" that with Redis or a last_seen
# column — both are explicit anti-goals (cost / trackable history).
PRESENCE_TTL_SECONDS = 60


def _key(mask_hash: str) -> str:
    return f"online:{mask_hash}"


def mark_online(mask_hash: str) -> None:
    """Refresh the mask's presence window (one cache write, ~1 microsecond)."""
    if mask_hash:
        cache.set(_key(mask_hash), 1, timeout=PRESENCE_TTL_SECONDS)


def is_online(mask_hash: str) -> bool:
    """True if the mask made any request within the last TTL seconds."""
    return bool(mask_hash) and cache.get(_key(mask_hash)) is not None


def online_hashes():
    """Best-effort list of mask hashes currently online (admin-only).

    LocMem keeps its expiry index in `_expire_info` ({full_key: expires_at},
    full_key = "<prefix>:<version>:online:<hash>"). Iterating it is
    introspection of a private attribute, acceptable for owner-facing admin
    features: guarded so any other cache backend (or an internals change)
    degrades to None ("unavailable") instead of breaking the admin.
    """
    expire_info = getattr(cache, "_expire_info", None)
    if expire_info is None:
        return None
    now = time.time()
    return [
        full_key.rsplit("online:", 1)[1]
        for full_key, expires_at in list(expire_info.items())
        if ":online:" in full_key and (expires_at is None or expires_at > now)
    ]


def online_count():
    """Best-effort count of masks currently online (admin dashboard only)."""
    hashes = online_hashes()
    return None if hashes is None else len(hashes)
