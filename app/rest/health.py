# Django
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK

# Libs
from app.permissions.client import IsClientAuthenticated


class HealthCheckView(APIView):
    """GET /health/ — liveness check. Returns 200 if the server responds,
    under the same auth + E2E encryption rules as every other endpoint."""

    permission_classes = (IsClientAuthenticated,)

    def get(self, request) -> Response:
        return Response({"status": "ok"}, status=HTTP_200_OK)
