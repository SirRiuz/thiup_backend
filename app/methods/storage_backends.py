# Python
import logging
import os

from decouple import config

# Django
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

# Libs
from app.methods import object_storage

logger = logging.getLogger(__name__)

# Internal route (NOT a business endpoint, NOT in the public API schema) that
# receives the local PUT and writes to MEDIA_ROOT — the local equivalent of the
# R2 PUT target. Registered only when the local backend is active (see
# core/urls.py + local_backend_active()).
LOCAL_UPLOAD_ROUTE = "__localupload__"


def build_object_key(token: str, ext: str) -> str:
    """Build a sharded object key from a random token.

    e.g. token 'RtfAnE4z...'  + ext 'webp'  (LEVELS=2, WIDTH=2)
         -> 'm/Rt/fA/RtfAnE4z....webp'

    The shard segments are a COPY of the token's first chars (deterministic from
    the token), so the path can always be rebuilt from it; the filename is the
    FULL token + extension. Chars are used as-is (keys are case-sensitive and
    '-'/'_' are valid). Layout knobs live in settings.
    """
    parts = [settings.STORAGE_KEY_PREFIX]
    for level in range(settings.STORAGE_SHARD_LEVELS):
        start = level * settings.STORAGE_SHARD_WIDTH
        segment = token[start : start + settings.STORAGE_SHARD_WIDTH]
        if segment:
            parts.append(segment)
    parts.append(f"{token}.{ext}")
    return "/".join(parts)


# Adapter interface + implementations.
class StorageBackend:
    """Storage abstraction behind the presign/confirm flow. The FE contract is
    identical regardless of the implementation.

    `base_url` (the backend origin, e.g. 'http://localhost:8080/') is passed by
    the view from the incoming request — the LOCAL backend needs it to build
    absolute URLs WITHOUT any configured env var; R2 ignores it.
    """

    def generate_upload(self, key, content_type, expires=None, base_url=None):
        """-> { upload_url, method, headers, expires_in }."""
        raise NotImplementedError

    def object_exists(self, key):
        """-> { content_length, content_type } if uploaded, else None."""
        raise NotImplementedError

    def public_url(self, key, base_url=None):
        """-> public URL to serve the object."""
        raise NotImplementedError

    def delete_object(self, key):
        """Permanently remove the object from storage (idempotent)."""
        raise NotImplementedError

    def delete_objects(self, keys):
        """Batch removal. Default: per-key fallback so every backend works;
        backends with a real batch API (R2/S3 DeleteObjects) override it."""
        for key in keys:
            self.delete_object(key)
        return len(keys)


class R2StorageBackend(StorageBackend):
    """S3-compatible (Cloudflare R2). Real presigned PUT + head_object. Uses the
    configured public base (AWS_S3_CUSTOM_DOMAIN), so base_url is ignored."""

    def generate_upload(self, key, content_type, expires=None, base_url=None):
        url = object_storage.generate_presigned_put(key, content_type, expires)
        return {
            "upload_url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_in": expires if expires is not None else settings.UPLOAD_PRESIGN_EXPIRES,
        }

    def object_exists(self, key):
        return object_storage.head_object(key)

    def public_url(self, key, base_url=None):
        return object_storage.public_url(key)

    def delete_object(self, key):
        return object_storage.delete_object(key)

    def delete_objects(self, keys):
        return object_storage.delete_objects(list(keys))


class LocalStorageBackend(StorageBackend):
    """DEV-ONLY fallback. Simulates the signed-URL flow on local disk: the
    "presigned" URL points back to an internal backend route that writes the
    bytes into MEDIA_ROOT, object_exists checks the file on disk, and public_url
    serves it from local media. NOTHING is sent to any bucket.

    The backend origin is AUTO-DETECTED from the request (base_url) — no env var
    to configure. Activated ONLY when no R2 is configured AND DEBUG (see
    get_backend). Never in production.
    """

    @staticmethod
    def _base(base_url):
        # Origin the browser used to reach this backend (from request). Empty
        # -> relative URLs (fine for same-origin fallbacks like the serializer).
        return (base_url or "").rstrip("/")

    def generate_upload(self, key, content_type, expires=None, base_url=None):
        return {
            "upload_url": f"{self._base(base_url)}/{LOCAL_UPLOAD_ROUTE}/{key}",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_in": expires if expires is not None else settings.UPLOAD_PRESIGN_EXPIRES,
        }

    def object_exists(self, key):
        path = local_path(key)
        if not path or not os.path.exists(path):
            return None
        # content_type unknown on disk; confirm only enforces it when present.
        return {"content_length": os.path.getsize(path), "content_type": None}

    def public_url(self, key, base_url=None):
        return f"{self._base(base_url)}{settings.MEDIA_URL}{key}"

    def delete_object(self, key):
        # Mirror R2's idempotency: deleting a missing file is not an error.
        path = local_path(key)
        if path and os.path.exists(path):
            os.remove(path)
        return True


# Backend selection.
def _r2_configured() -> bool:
    """All required R2 credentials present."""
    return all(
        config(name, default="")
        for name in (
            "AWS_S3_ENDPOINT_URL",
            "AWS_STORAGE_BUCKET_NAME",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        )
    )


def local_backend_active() -> bool:
    """True when the LOCAL debug backend is the one in use — used by urls.py to
    register the local PUT receiver ONLY then. Never raises."""
    return not _r2_configured() and bool(settings.DEBUG) and not settings.ENCRYPTED_RESPONSE


_backend = None


def get_backend() -> StorageBackend:
    """Pick the backend automatically:
    - R2 fully configured        -> R2StorageBackend (prod + any env).
    - else, DEBUG (local)        -> LocalStorageBackend (disk simulation).
    - else (prod, no R2)         -> hard error (never write to the prod box).
    """
    global _backend
    if _backend is not None:
        return _backend

    if _r2_configured():
        _backend = R2StorageBackend()
    elif settings.DEBUG:
        # The local PUT receiver takes a RAW body; the request-crypto middleware
        # would reject a raw PUT when ENCRYPTED_RESPONSE is on (and we must not
        # touch that middleware). So local mode requires plain transport.
        if settings.ENCRYPTED_RESPONSE:
            raise ImproperlyConfigured(
                "Local debug storage needs ENCRYPTED_RESPONSE=False (the raw PUT "
                "to the internal upload route can't be decrypted). Set "
                "ENCRYPTED_RESPONSE=False or configure R2."
            )
        logger.warning(
            "Object storage not configured: using LOCAL disk storage "
            "(DEBUG only). Files are written to MEDIA_ROOT, not a bucket."
        )
        _backend = LocalStorageBackend()
    else:
        raise ImproperlyConfigured(
            "Object storage (R2) is not configured. Refusing to fall back to local disk outside DEBUG."
        )
    return _backend


# Local PUT receiver (the local equivalent of R2's PUT target).
def local_path(key: str):
    """Absolute path under MEDIA_ROOT for `key`, or None if it escapes it
    (path-traversal guard)."""
    root = os.path.realpath(settings.MEDIA_ROOT)
    target = os.path.realpath(os.path.join(root, key))
    if target != root and not target.startswith(root + os.sep):
        return None
    return target


@csrf_exempt
def local_upload_put(request, key):
    """Receives the local PUT and writes the bytes to MEDIA_ROOT/<key>,
    imitating R2. Active only in local debug mode (registered by urls.py).

    Streams the body to disk in chunks so a large upload never loads fully into
    memory (RAM is limited); rejects early on the declared Content-Length."""
    if request.method != "PUT":
        return HttpResponseNotAllowed(["PUT"])

    # Reject on the declared length before reading anything into memory.
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > settings.UPLOAD_MAX_BYTES:
        return JsonResponse({"detail": "Uploaded object is too large."}, status=413)

    path = local_path(key)
    if path is None:
        return JsonResponse({"detail": "Invalid key."}, status=400)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Stream the body in fixed 1 MB chunks via request.read() (do NOT touch
    # request.body — that would buffer everything), stopping if the actual
    # stream exceeds the cap.
    chunk_size = 1024 * 1024
    written = 0
    with open(path, "wb") as handle:
        while True:
            chunk = request.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > settings.UPLOAD_MAX_BYTES:
                handle.close()
                os.remove(path)
                return JsonResponse({"detail": "Uploaded object is too large."}, status=413)
            handle.write(chunk)
    return JsonResponse({"status": "ok"}, status=200)
