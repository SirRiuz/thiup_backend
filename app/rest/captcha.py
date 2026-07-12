# Django
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)

# Libs
from app.permissions.client import IsClientAuthenticated
from app.methods.captcha import (
    verify_captcha_token,
    issue_human_pass,
    CaptchaUnavailable,
)


class CaptchaVerifyView(APIView):
    """
    POST /captcha/verify/ — exchanges a single-use Cap token for the
    short-lived, mask-bound HUMAN PASS that entity-creating writes require
    (see @human_validator). The FE widget solves the invisible proof-of-work
    directly against the Cap standalone server; this is the only place the
    backend talks to Cap (siteverify), so a Cap outage blocks pass RENEWALS
    only — passes already issued keep working until they expire.
    ---
    Request Body:

            {"token": "<single-use Cap token from the widget>"}

    Response codes:

        200 - {"pass": "<jwt>", "expires_in": <seconds>}.
        403 - Invalid/consumed Cap token (code CAPTCHA_FAILED).
        404 - Captcha protection disabled (code CAPTCHA_DISABLED).
        503 - Cap server unreachable (code CAPTCHA_UNAVAILABLE).
    """

    permission_classes = (IsClientAuthenticated,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "captcha"

    def post(self, request) -> (Response):
        # Same signal pattern as the gateway when its flag is off: the
        # endpoint "does not exist" while the feature is disabled.
        if not settings.CAPTCHA_PROTECT:
            return Response(
                {"code": "CAPTCHA_DISABLED"},
                status=HTTP_404_NOT_FOUND,
            )

        token = request.data.get("token", "") \
            if isinstance(request.data, dict) else ""
        try:
            if not verify_captcha_token(token):
                return Response(
                    {
                        "detail": "Captcha verification failed.",
                        "code": "CAPTCHA_FAILED",
                    },
                    status=HTTP_403_FORBIDDEN,
                )
        except CaptchaUnavailable:
            return Response(
                {
                    "detail": "Captcha verification unavailable.",
                    "code": "CAPTCHA_UNAVAILABLE",
                },
                status=HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "pass": issue_human_pass(request.mask),
                "expires_in": settings.CAPTCHA_PASS_TTL_SECONDS,
            },
            status=HTTP_200_OK,
        )
