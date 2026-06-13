# Python
import io
import json
import logging

# Django
from django.conf import settings
from django.urls import resolve, Resolver404
from django.http import QueryDict

# REST
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)

# Libs
from app.methods.gateway_path import token_matches


logger = logging.getLogger(__name__)


class GatewayView(APIView):
    """
    GATEWAY de transporte con PATH ÚNICO POR REQUEST (POST /{token}/). Único
    endpoint por el que pasa TODO el tráfico cuando ENCRYPTED_RESPONSE está
    activo, INCLUIDOS ticket y config. Cada petición usa un /{token}/
    DISTINTO → genera RUIDO: ensucia logs, confunde a observadores casuales
    y ningún path revela el endpoint real.

    VERIFICAR, NO REGISTRAR: los paths únicos no se pueden registrar (serían
    infinitos). Una ruta COMODÍN de un segmento [0-9a-f]{24} captura el
    {token}; aquí se RECOMPUTA token = HMAC(GATEWAY_SEED, nonce) a partir del
    nonce que viaja DENTRO del sobre cifrado y se compara en TIEMPO CONSTANTE
    con el {token} de la URL. Match → despacha; no → 404 (sin pistas).

    El sobre (desenvuelto por RequestDecryptMiddleware ANTES de llegar aquí):
        {"method","path","query","body","nonce"}
    y se DESPACHA INTERNAMENTE (sin redirect): se reescribe el HttpRequest y
    se re-resuelve la vista destino con django.urls.resolve.

    ANTI-REPLAY: el {token} de la URL es RUIDO, NO anti-replay. Reenviar un
    sobre capturado lo rechaza SINGLE_REQUEST_PROTECT en la vista interna
    (el ticket JWT es de vida corta) — protección INDEPENDIENTE del nonce.

    AUTH: el gateway es AllowAny a propósito (rompe el huevo-y-la-gallina:
    el ticket se obtiene VÍA gateway sin tener ticket aún). La protección
    single-request la aplica la VISTA INTERNA (/ticket/ es AllowAny; el resto
    exige el client-assertion del header).

    Anti-SSRF: solo despacha a vistas de `app.rest`; nunca a externas, ni a
    admin/honeypot/swagger, ni a sí mismo (recursión).
    """

    permission_classes = (AllowAny,)

    ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    ALLOWED_VIEW_MODULE = "app.rest"

    def post(self, request, gw_hash="") -> (Response):
        # Toggle: el gateway SOLO opera con el cifrado activo. Apagado → 404
        # IDENTIFICABLE (code=GATEWAY_DISABLED): la señal INEQUÍVOCA para que
        # el FE infiera "modo normal, usa directo". Cualquier OTRO 404 (token
        # inválido, ruta inexistente) es genérico y NO debe confundirse con
        # esta señal → el FE se queda en cifrado (fail-closed) ante esos.
        if not settings.ENCRYPTED_RESPONSE:
            return self._reject(
                "gateway_disabled", HTTP_404_NOT_FOUND, code="GATEWAY_DISABLED")

        envelope = request.data  # JSON ya descifrado por el middleware
        if not isinstance(envelope, dict):
            return self._reject("invalid_envelope", HTTP_400_BAD_REQUEST)

        # Path único verificable: el {token} de la URL debe corresponder al
        # nonce del sobre (recomputo en tiempo constante). No casa → 404.
        if not token_matches(gw_hash, envelope.get("nonce")):
            return self._reject("token_mismatch", HTTP_404_NOT_FOUND)

        method = str(envelope.get("method") or "GET").upper()
        path = envelope.get("path") or ""
        query = envelope.get("query") or ""
        body = envelope.get("body")  # str | None

        # ── Validación del destino (anti-SSRF, sin loggear el path) ──────
        if method not in self.ALLOWED_METHODS:
            return self._reject("method_not_allowed", HTTP_400_BAD_REQUEST)
        if (not isinstance(path, str) or not path.startswith("/")
                or path.startswith("//") or "://" in path):
            return self._reject("invalid_path", HTTP_400_BAD_REQUEST)

        try:
            match = resolve(path)
        except Resolver404:
            return self._reject("unresolved_path", HTTP_404_NOT_FOUND)

        # Allowlist: SOLO vistas del propio proyecto (app.rest). Bloquea
        # admin/honeypot/swagger/estáticos y cualquier no-DRF (sin .cls).
        view_cls = getattr(match.func, "cls", None)
        if view_cls is None or not view_cls.__module__.startswith(
                self.ALLOWED_VIEW_MODULE):
            return self._reject("forbidden_target", HTTP_404_NOT_FOUND)
        # Guard de recursión: nunca re-despachar al propio gateway.
        if view_cls is GatewayView:
            return self._reject("recursive_dispatch", HTTP_400_BAD_REQUEST)

        self._rewrite_request(request._request, method, path, query, body)

        try:
            # Despacho INTERNO: lo que hace el resolver de Django tras
            # emparejar la URL, pero dentro de este mismo request.
            return match.func(request._request, *match.args, **match.kwargs)
        except Exception:
            logger.warning("gateway rejected: reason=dispatch_error")
            return self._reject("dispatch_error", HTTP_400_BAD_REQUEST)

    def _rewrite_request(self, dj, method, path, query, body) -> (None):
        """
        Reescribe el HttpRequest subyacente para que la vista destino lo vea
        como si la URL real hubiese llegado. El body interno ya está EN
        CLARO (venía dentro del sobre); se entrega como application/json.
        """
        dj.method = method
        dj.path = path
        dj.path_info = path
        dj.META["PATH_INFO"] = path
        dj.META["QUERY_STRING"] = query
        dj.GET = QueryDict(query, mutable=False)

        if body is None:
            raw = b""
        elif isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            raw = json.dumps(body, separators=(",", ":")).encode("utf-8")

        dj._body = raw
        dj._stream = io.BytesIO(raw)
        dj._read_started = False
        # Invalida cualquier parseo previo del POST del propio gateway.
        for attr in ("_post", "_files"):
            if hasattr(dj, attr):
                delattr(dj, attr)
        dj.META["CONTENT_TYPE"] = "application/json"
        dj.META["CONTENT_LENGTH"] = str(len(raw))

    def _reject(self, reason, status, code=None) -> (Response):
        # Log SIN el path/params descifrados (privacidad): solo el motivo.
        logger.warning("gateway rejected: reason=%s", reason)
        payload = {"detail": "Gateway dispatch error."}
        # `code` SOLO se incluye para gateway_disabled (señal de modo normal).
        # El resto de rechazos quedan genéricos (sin código, sin filtrar nada).
        if code:
            payload["code"] = code
        return Response(payload, status=status)
