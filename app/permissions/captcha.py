# Python
import functools

# Django
from django.conf import settings
from rest_framework.exceptions import APIException, PermissionDenied

# Libs
from app.methods.captcha import validate_human_pass

# Header carrying the human pass minted by POST /captcha/verify/.
PASS_HEADER = "X-Human-Pass"


class CaptchaUnavailableError(APIException):
    """
    Fail-closed when the Cap server is unreachable: 503 with a stable code
    so the FE can distinguish "try again shortly" from "renew your pass"
    (403 CAPTCHA_FAILED). Same machine-readable pattern as GATEWAY_DISABLED.
    """

    status_code = 503
    default_detail = {
        "detail": "Captcha verification unavailable.",
        "code": "CAPTCHA_UNAVAILABLE",
    }
    default_code = "captcha_unavailable"


def human_validator(view_method):
    """
    Marks a ViewSet action as human-only: the request must carry a valid,
    unexpired human pass (X-Human-Pass) minted by POST /captcha/verify/
    after solving an invisible Cap proof-of-work. Deliberately a per-action
    decorator — removing the line unprotects that single endpoint, without
    touching the rest of the ViewSet. No-op when CAPTCHA_PROTECT is off.
    The pass is validated locally (JWT signature + expiry + mask binding):
    no network call on the write path. Never logged nor echoed.
    """

    @functools.wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        if settings.CAPTCHA_PROTECT:
            token = request.headers.get(PASS_HEADER, "")
            if not validate_human_pass(token, request.mask):
                raise PermissionDenied(
                    {
                        "detail": "Captcha verification failed.",
                        "code": "CAPTCHA_FAILED",
                    }
                )
        return view_method(self, request, *args, **kwargs)

    return wrapper
