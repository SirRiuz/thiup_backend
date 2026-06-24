# Python
import logging

# Django
from django.conf import settings
from decouple import config

# Libs
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


# Presigned-upload settings. These reuse the project's existing S3-compatible
# storage env (AWS_* — the same vars wired in core/settings.py for Cloudflare
# R2; see .env.template "Object storage: S3-compatible"). R2 has no IAM, so the
# access keys are required. Read here (not only inside the settings
# USE_AWS_STORAGE block) so presigning works regardless of that flag.
def _endpoint_url():
    return config("AWS_S3_ENDPOINT_URL", default="") or None


def _bucket():
    return config("AWS_STORAGE_BUCKET_NAME", default="") or None


def _public_base():
    """Public base for object URLs (CDN/custom domain). Scheme optional."""
    base = (config("AWS_S3_CUSTOM_DOMAIN", default="") or "").strip().rstrip("/")
    if base and not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    return base


# Cached boto3 client (building one is relatively expensive).
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=_endpoint_url(),
            aws_access_key_id=config("AWS_ACCESS_KEY_ID", default="") or None,
            aws_secret_access_key=config("AWS_SECRET_ACCESS_KEY", default="") or None,
            region_name=config("AWS_S3_REGION_NAME", default="auto"),
            # s3v4 is required to presign; the checksum flags mirror
            # AWS_S3_CLIENT_CONFIG in settings (R2 rejects boto3's default
            # integrity checksums).
            config=Config(
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
    return _client


def public_url(key: str) -> str:
    """Public URL of an object from its key (via the configured custom domain)."""
    base = _public_base()
    return f"{base}/{key}" if base else key


def generate_presigned_put(key: str, content_type: str, expires: int = None) -> str:
    """Presigned PUT URL bound to Bucket + Key + ContentType. The client MUST
    send the SAME Content-Type in the PUT or the upload is rejected by R2."""
    client = _get_client()
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": _bucket(),
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=(expires if expires is not None else settings.UPLOAD_PRESIGN_EXPIRES),
    )


def head_object(key: str):
    """Return the object's head metadata (dict) if it exists, else None.

    Used to VERIFY the client really uploaded before persisting the record —
    never trust the client's word that the PUT succeeded.
    """
    client = _get_client()
    try:
        response = client.head_object(Bucket=_bucket(), Key=key)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        # Unexpected error (auth/network/misconfig): surface as not-found so the
        # confirm fails closed rather than activating an unverified record.
        logger.warning("head_object failed: code=%s", code)
        return None
    return {
        "content_length": response.get("ContentLength"),
        "content_type": response.get("ContentType"),
    }
