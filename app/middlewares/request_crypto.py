# Python
import base64
import io
import json
import logging

# Libs
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# Django
from django.conf import settings
from django.http import JsonResponse
from django.urls import Resolver404, resolve

logger = logging.getLogger(__name__)


def _decrypt_request(key_b64, ciphertext_b64, iv_b64) -> str:
    """
    Descifra AES-CBC y QUITA el padding PKCS7 — espejo exacto del
    `encryptor` (que hace pad()). Nota: kdf.decryptor NO desempaqueta el
    padding (bug latente, estaba sin uso); aquí se hace bien para que el
    JSON quede limpio.
    """
    cipher = AES.new(base64.b64decode(key_b64), AES.MODE_CBC, base64.b64decode(iv_b64))
    plain = unpad(cipher.decrypt(base64.b64decode(ciphertext_b64)), AES.block_size)
    return plain.decode("utf-8")


class RequestDecryptMiddleware:
    """
    Descifra AUTOMÁTICAMENTE el body cifrado del request y entrega a las
    vistas el JSON plano — transparente para ellas. Espejo SIMÉTRICO del
    cifrado de respuesta (EncodeRenderer): mismo AES-CBC/PKCS7 + base64 +
    reverse; reutiliza el `decryptor` existente (cero cripto nueva en el
    BE). NO sustituye a TLS: es opacidad de transporte (la clave viaja en
    el sobre del request, igual que en la respuesta).

    Gobernado por ENCRYPTED_RESPONSE:
      - True: si el request trae sobre cifrado (header X-Request-Payload),
        se descifra y se reemplaza el body por el JSON plano. Un body SIN
        cifrar en un endpoint NO exento → 400 (enforcement: el toggle no
        es decorativo).
      - False: no se toca nada (modo plano).

    EXENTOS (nunca exigen body cifrado):
      - Requests sin body (GET/DELETE/HEAD/OPTIONS): no hay nada que cifrar.
      - /ticket/ y /config/: bootstrap del esquema (GET sin body de todos
        modos); contrato fijo, independientes de los flags.
      - Real admin (obfuscated settings.INTERNAL_ADMIN_URL) and honeypot
        (/admin/): server-rendered HTML with plain forms via the normal Django
        cycle (CSRF + session). Identified by the resolved URL app_name, not by
        hardcoded prefixes. E2E encryption is for the SPA/API; these surfaces
        keep their native Django protections.
    """

    HEADER = "HTTP_X_REQUEST_PAYLOAD"
    # Métodos que pueden traer body cifrado y, por tanto, se someten al
    # enforcement cuando el flag está activo.
    BODY_METHODS = ("POST", "PUT", "PATCH")
    # Prefijos exentos del enforcement (bootstrap). Son GET sin body, pero
    # se listan explícitos por robustez.
    EXEMPT_PREFIXES = ("/ticket/", "/config/", "/health/")
    # Server-rendered HTML surfaces, identified by the app_name that Django's
    # URLconf resolves to (NOT by hardcoded path prefixes): the real admin
    # (obfuscated path) and the honeypot. They use the normal Django cycle
    # (CSRF + session + forms) and never carry encrypted bodies.
    EXEMPT_APP_NAMES = ("admin", "honeypot")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.ENCRYPTED_RESPONSE and request.method in self.BODY_METHODS:
            error = self._maybe_decrypt(request)
            if error is not None:
                return error
        return self.get_response(request)

    def _is_exempt(self, request) -> bool:
        # Bootstrap API endpoints: cheap prefix check.
        if any(request.path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return True
        # Server-rendered HTML surfaces (real admin + honeypot): let Django's
        # own URLconf classify the request by app_name instead of duplicating
        # routes here. Same resolve() approach the gateway uses.
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return False
        return match.app_name in self.EXEMPT_APP_NAMES

    def _maybe_decrypt(self, request):
        payload = request.META.get(self.HEADER)

        if not payload:
            # Sin sobre cifrado. Body vacío o endpoint exento → se deja
            # pasar (un POST sin cuerpo es válido). Body en claro en un
            # endpoint protegido → 400 (sin eco del body, sin stacktrace).
            if self._is_exempt(request) or not request.body:
                return None
            # Log de diagnóstico SIN contenido del body (privacidad): solo
            # el motivo técnico y la ruta. Confirma "llegó texto plano".
            logger.warning(
                "request_crypto rejected: reason=plaintext_on_protected_endpoint method=%s path=%s",
                request.method,
                request.path,
            )
            return JsonResponse({"detail": "Encrypted request body required."}, status=400)

        # Distinguimos las dos fallas del sobre para diagnóstico (sin tocar
        # la respuesta al cliente, que sigue siendo un 400 genérico).
        try:
            # Espejo del EncodeRenderer: header = base64(key:iv) invertido;
            # body = base64(ciphertext) invertido.
            real_payload = payload[::-1]
            key_b64, iv_b64 = base64.b64decode(real_payload).decode().split(":")
            ciphertext_b64 = request.body.decode()[::-1]
            plain = _decrypt_request(key_b64, ciphertext_b64, iv_b64)
        except Exception:
            # Sobre presente pero indescifrable: clave/iv/ciphertext corruptos
            # o doble cifrado. NO se loggea contenido, solo el motivo.
            logger.warning(
                "request_crypto rejected: reason=envelope_decrypt_failed method=%s path=%s",
                request.method,
                request.path,
            )
            return JsonResponse({"detail": "Malformed encrypted request body."}, status=400)

        try:
            # Validar que es JSON (descarta basura sin filtrar contenido).
            json.loads(plain)
        except Exception:
            logger.warning(
                "request_crypto rejected: reason=decrypted_not_json method=%s path=%s",
                request.method,
                request.path,
            )
            return JsonResponse({"detail": "Malformed encrypted request body."}, status=400)

        # Reemplazar el body por el JSON plano para que DRF lo parsee
        # normal — las vistas no se enteran del cifrado.
        plain_bytes = plain.encode()
        request._body = plain_bytes
        request._stream = io.BytesIO(plain_bytes)
        request._read_started = False
        request.META["CONTENT_TYPE"] = "application/json"
        request.META["CONTENT_LENGTH"] = str(len(plain_bytes))
        return None
