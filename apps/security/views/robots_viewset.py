# Django
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.status import *


# Libs
from apps.security.throttling import CustomAnonRateThrottle


class Robots(APIView):

    permission_classes = (AllowAny,)

    def get_throttles(self):
        throttle = CustomAnonRateThrottle(allow_all=True)
        return (throttle,)

    def post(self, request) -> (Response):
        """
        Check if the client is a robot
        ---
        Content/Type:
            application/json
        ---
        response code: 200
        """
        throttle = self.get_throttles()[0]
        throttle.allow(request)
        return Response(status=HTTP_200_OK)
