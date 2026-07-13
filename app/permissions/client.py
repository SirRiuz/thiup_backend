# Django
from django.conf import settings

# Libs
from jwt.exceptions import PyJWTError
from rest_framework.permissions import BasePermission

from app.methods.tokens import decode_token


class IsClientAuthenticated(BasePermission):
    """
    Access only with a Client-assertion ISSUED BY THE SERVER
    (GET /ticket/, signed with API_SECRET_KEY server-side and short-lived).
    The client NEVER holds the secret: it only carries the ticket, which
    it cannot forge. decode_token verifies signature + exp (jwt.decode fails
    if expired). This replaces the previous scheme, in which the FE signed
    with a shared secret extractable from the bundle.

    NOT anti-replay / NOT single-use: a valid, unexpired ticket is accepted
    on any number of requests (the FE deliberately reuses one ticket for its
    whole TTL). The jti minted in issue_ticket() is NOT tracked. This gates
    casual scripting (a client must fetch and rotate a ticket), not a
    determined attacker; TLS is the real transport boundary.
    """

    TOKEN_TYPE = "Client-assertion"

    def has_permission(self, request, view) -> bool:
        if not settings.SINGLE_REQUEST_PROTECT:
            return True

        client_token = request.headers.get(self.TOKEN_TYPE, "")
        try:
            decode_token(client_token)
            return True
        except PyJWTError:
            return False
