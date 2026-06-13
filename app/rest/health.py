# Django
from django.db import connection
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

# Libs
from app.permissions.client import IsClientAuthenticated


class HealthCheckView(APIView):
    """
    GET /health/ — with the SAME standard rules as any endpoint in the
    app (no special treatment):

      - PRIVATE: IsClientAuthenticated (signed client-assertion, single
        request pattern with anti-replay via cache — same as the rest).
      - E2E ENCRYPTED: the default renderer (EncodeRenderer when
        ENCRYPTED_RESPONSE) encrypts the response as in every endpoint.

    The frontend only consumes the STATUS: a 200 means the server
    responds, the DB connects, the auth is valid and the encryption works — the
    complete verification. DB down → controlled 503.

    (The permission doesn't touch the DB —tokens in cache—, so the 503 for a
    down DB is still reachable.)
    """

    permission_classes = (IsClientAuthenticated,)

    def get(self, request) -> Response:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            return Response(
                {"status": "error"}, status=HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response({"status": "ok"}, status=HTTP_200_OK)
