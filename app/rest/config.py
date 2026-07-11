# Django
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK
from rest_framework.permissions import AllowAny


class ConfigView(APIView):
    """
    GET /config/ — flags de transporte que gobiernan a los DEMÁS endpoints.
    El FE lo consulta UNA vez al arrancar (en DIRECTO/legible) para conocer
    ENCRYPTED_RESPONSE y fijar el modo de transporte de todo lo demás.

    METADATA PÚBLICA (resuelve el huevo-y-la-gallina): es solo configuración
    sin datos sensibles, así que es AllowAny — se puede leer SIN ticket. Eso
    permite la lectura directa del bootstrap sin depender del transporte que
    el propio flag decide. El RENDER sigue el flag como todo lo demás
    (EncodeRenderer cuando ENCRYPTED_RESPONSE=True, JSON plano cuando False) —
    NO es un contrato fijo aparte. El parseResponseBody del FE decodifica
    ambos casos. GET sin body → no necesita descifrado de request.
    """

    permission_classes = (AllowAny,)

    def get(self, request) -> (Response):
        return Response(
            {
                "encrypted_response": settings.ENCRYPTED_RESPONSE,
                "single_request_protect": settings.SINGLE_REQUEST_PROTECT,
                # Captcha bootstrap (additive keys — the two above are a
                # frozen contract). captcha_endpoint is the full public API
                # endpoint for @cap.js/widget (data-cap-api-endpoint): the
                # FE needs zero extra config. Public metadata only — the
                # backend-only CAP_SECRET never leaves the server.
                "captcha_protect": settings.CAPTCHA_PROTECT,
                "captcha_endpoint": (
                    f"{settings.CAP_PUBLIC_URL.rstrip('/')}/"
                    f"{settings.CAP_SITE_KEY}/"
                    if settings.CAPTCHA_PROTECT else None
                ),
            },
            status=HTTP_200_OK,
        )
