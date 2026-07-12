# Python
import uuid
from datetime import datetime, timedelta, timezone

# Libs
import jwt

# Django
from django.conf import settings

# Lifetime of the server-issued ticket (Client-assertion). Short: the
# client carries and refreshes it; it's not a secret, it's an ephemeral bearer.
TICKET_TTL_SECONDS = 300


def issue_ticket() -> str:
    """
    Issues a Client-assertion ticket signed with the SERVER's secret
    (API_SECRET_KEY, server-side only). The client NEVER signs or knows
    the secret — it only receives and carries this opaque token. Short
    exp + unique jti: not forgeable by the client, expires on its own.
    """
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + timedelta(seconds=TICKET_TTL_SECONDS),
        },
        settings.API_SECRET_KEY,
        algorithm="HS256",
    )


def encode_token(data) -> str:
    """Create new jwt token"""
    return jwt.encode(data, settings.API_SECRET_KEY, algorithm="HS256")


def decode_token(token) -> dict:
    """Decode the client access token"""
    return jwt.decode(token, settings.API_SECRET_KEY, algorithms=["HS256"])
