# Python
import uuid
import logging
from datetime import datetime, timedelta, timezone

# Django
from django.conf import settings

# Libs
import requests
from jwt.exceptions import PyJWTError
from app.methods.tokens import encode_token, decode_token

logger = logging.getLogger(__name__)

# One bounded attempt, no retries: Cap tokens are single-use, so a retry
# after a half-completed verify would fail server-side anyway.
VERIFY_TIMEOUT_SECONDS = 3

# Claim that distinguishes a human pass from other API_SECRET_KEY-signed
# tokens (e.g. the /ticket/ client-assertion, which carries no purpose).
PASS_PURPOSE = "human_pass"

# Module-level session: reuses the TCP connection to the Cap server across
# verifies within a worker (urllib3's pool is thread-safe; siteverify sets
# no cookies, so the shared jar is inert).
_session = requests.Session()


class CaptchaUnavailable(Exception):
    """The Cap standalone server could not be reached or answered 5xx."""


def verify_captcha_token(token) -> (bool):
    """
    Redeem a single-use Cap token against the standalone siteverify
    endpoint. Returns True only on an explicit {"success": true}; Cap
    consumes the token on verification, so replay is impossible.
    Privacy: the token and the secret are NEVER logged or echoed.
    """
    if not token:
        return False

    url = (
        f"{settings.CAP_SITEVERIFY_URL.rstrip('/')}"
        f"/{settings.CAP_SITE_KEY}/siteverify"
    )
    try:
        response = _session.post(
            url,
            json={"secret": settings.CAP_SECRET, "response": token},
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        # Generic reason only — exception bodies may echo the payload.
        logger.warning("captcha verify failed: reason=unreachable")
        raise CaptchaUnavailable()

    if response.status_code >= 500:
        logger.warning("captcha verify failed: reason=server_error")
        raise CaptchaUnavailable()

    try:
        return bool(response.json().get("success"))
    except ValueError:
        return False


def issue_human_pass(mask) -> (str):
    """
    Mint the short-lived human pass granted by one solved captcha. Bound
    to the requester's mask so it cannot be shared across IPs; expiry is
    the only revocation (same stateless model as the /ticket/ JWT).
    """
    now = datetime.now(timezone.utc)
    return encode_token(
        {
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + timedelta(
                seconds=settings.CAPTCHA_PASS_TTL_SECONDS),
            "purpose": PASS_PURPOSE,
            "mask": mask.hash,
        }
    )


def validate_human_pass(token, mask) -> (bool):
    """
    Check signature + expiry (decode_token raises on both) and that the
    pass was minted as a human pass for THIS mask — a /ticket/ JWT or a
    pass stolen from another IP must not validate. Never logs the token.
    """
    try:
        payload = decode_token(token)
    except PyJWTError:
        return False
    return (
        payload.get("purpose") == PASS_PURPOSE
        and payload.get("mask") == mask.hash
    )
