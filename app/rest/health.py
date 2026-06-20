# Django
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK
from rest_framework.permissions import AllowAny


class HealthCheckView(APIView):
    """GET /health/ — liveness probe. Public (AllowAny): the ALB/ELB health
    checker can't send a Client-assertion ticket, so this must NOT require it
    even when SINGLE_REQUEST_PROTECT is on. Returns 200 if the server responds."""

    permission_classes = (AllowAny,)

    def get(self, request) -> Response:
        return Response({"status": "ok"}, status=HTTP_200_OK)
