# Django
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework.status import HTTP_200_OK

# Libs
from app.methods.tokens import issue_ticket


class TicketView(APIView):
    """
    GET /ticket/ — issues the Client-assertion (BOOTSTRAP of the
    SINGLE_REQUEST_PROTECT scheme). The backend signs a short-lived ticket with
    ITS secret (API_SECRET_KEY, server-side); the client only carries it.

    AllowAny on purpose: it's the starting point, it can't require a
    ticket (infinite regression). Defense against abuse: server-side
    rate limit (AnonRateThrottle) + the ticket's short lifetime.

    Respuesta CIFRADA con el render estándar del proyecto (EncodeRenderer
    + X-Response-Payload cuando ENCRYPTED_RESPONSE): se quitó la excepción
    JSONRenderer para unificar el transporte con el resto de la API — el
    JWT ya no viaja en claro. El cliente lo descifra por el MISMO decode
    (parseResponseBody). NO sustituye a TLS; es opacidad de transporte.
    """

    permission_classes = (AllowAny,)
    throttle_classes = (AnonRateThrottle,)

    def get(self, request) -> (Response):
        return Response({"ticket": issue_ticket()}, status=HTTP_200_OK)
