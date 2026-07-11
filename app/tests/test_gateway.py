# Python
import json
import base64
from datetime import datetime

# Django
from django.test import Client, TestCase, override_settings
from rest_framework import status

# Libs
import secrets
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from app.methods.tokens import encode_token
from app.methods.gateway_path import derive_token

# Models
from app.models.mask import Mask
from app.models.reaction import Reaction

# Reutilizamos el decode de respuestas de la suite existente.
from app.tests.test_foryou import decode_body


client = Client()


def _seal(plain_str):
    """Cifra un string como el FE (X-Request-Payload + body invertido)."""
    key, iv = get_random_bytes(16), get_random_bytes(16)
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plain_str.encode(), 16))
    header = base64.b64encode(
        f"{base64.b64encode(key).decode()}:{base64.b64encode(iv).decode()}"
        .encode()).decode()[::-1]
    return base64.b64encode(ct).decode()[::-1], header


def gateway_post(method, path, token=None, query="", body=None,
                 url_path=None, extra=None):
    """Posts through the GATEWAY (per-request UNIQUE path) like the FE:
    generates a nonce, computes token = HMAC(seed, nonce), puts the nonce
    INSIDE the envelope and POSTs to /{token}/. `url_path` forces a different
    URL path (to exercise the token↔nonce mismatch). `extra` merges additional
    client kwargs (e.g. HTTP_X_HUMAN_PASS) — headers ride OUTSIDE the
    envelope, like the ticket."""
    nonce = secrets.token_hex(16)
    inner = json.dumps({
        "method": method, "path": path, "query": query,
        "body": body, "nonce": nonce})
    data, header = _seal(inner)
    real_path = url_path if url_path is not None else f"/{derive_token(nonce)}/"
    client_kwargs = dict(extra) if extra else {}
    if token is not None:
        client_kwargs["HTTP_CLIENT_ASSERTION"] = token
    return client.post(
        real_path,
        data=data,
        content_type="application/raw",
        HTTP_X_REQUEST_PAYLOAD=header,
        **client_kwargs,
    )


def token():
    return encode_token({"timestamp": datetime.now().__str__()})


class GatewayDispatchTest(TestCase):
    """Path rotativo + dispatch interno + ventana de gracia + anti-SSRF."""

    def setUp(self):
        self.author = Mask.objects.create(hash="gw-author", country_code="CO")
        # `love` is seeded by migration 0015; reuse it instead of recreating.
        Reaction.objects.get_or_create(name="love", defaults={"emoji": "❤️"})

    # ── Dispatch interno entrega la respuesta del destino real ───────────
    def test_dispatch_to_foryou_returns_real_response(self):
        res = gateway_post("POST", "/threads/foryou/", token(), body="{}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", decode_body(res))

    def test_dispatch_preserves_query_params(self):
        """?page=2 (dentro del sobre) sobre feed vacío → 404 de paginación
        de DRF (prueba que el query viajó hasta la vista)."""
        res = gateway_post("POST", "/threads/foryou/", token(),
                           query="page=2", body="{}")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_http_redirect(self):
        res = gateway_post("POST", "/threads/foryou/", token(), body="{}")
        self.assertNotIn(res.status_code, (301, 302, 303, 307, 308))
        self.assertFalse(res.has_header("Location"))

    # ── Ticket/config VÍA gateway (bootstrap, sin huevo-y-la-gallina) ────
    def test_ticket_via_gateway_without_assertion(self):
        """/ticket/ se obtiene VÍA gateway SIN tener ticket previo (el
        gateway es AllowAny y la vista interna /ticket/ también)."""
        res = gateway_post("GET", "/ticket/", token=None)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("ticket", decode_body(res))

    def test_config_via_gateway_with_assertion(self):
        res = gateway_post("GET", "/config/", token())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("encrypted_response", decode_body(res))

    # ── Path ÚNICO POR REQUEST: verificación token↔nonce ─────────────────
    def test_token_must_match_nonce(self):
        """El {token} de la URL debe corresponder al nonce del sobre. Un
        token que NO casa (recomputo) → 404 GENÉRICO, sin el código
        GATEWAY_DISABLED (no se debe confundir con gateway-desactivado)."""
        res = gateway_post("POST", "/threads/foryou/", token(), body="{}",
                           url_path=f"/{secrets.token_hex(12)}/")  # 24 hex random
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        # Cuerpo cifrado (flag on) y SIN código de modo: ambiguo para el FE.
        self.assertNotIn("code", decode_body(res))

    def test_each_request_uses_a_distinct_path(self):
        """RUIDO: dos peticiones (mismo destino) salen por /{token}/ DISTINTO
        y ambas despachan correctamente."""
        a = gateway_post("POST", "/threads/foryou/", token(), body="{}")
        b = gateway_post("POST", "/threads/foryou/", token(), body="{}")
        self.assertEqual(a.status_code, status.HTTP_200_OK)
        self.assertEqual(b.status_code, status.HTTP_200_OK)

    # ── Anti-SSRF / allowlist ────────────────────────────────────────────
    def test_external_absolute_url_rejected(self):
        res = gateway_post("POST", "http://evil.com/steal", token(), body="{}")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unresolved_path_rejected(self):
        res = gateway_post("POST", "/does/not/exist/", token(), body="{}")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_target_forbidden(self):
        res = gateway_post("GET", "/admin/", token())
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_self_dispatch_rejected(self):
        """Re-despacho al propio gateway (destino = un /{token}/ que resuelve
        al comodín → GatewayView) → recursión bloqueada por el guard."""
        res = gateway_post("POST", f"/{secrets.token_hex(12)}/", token(),
                           body="{}")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Single-request a través del gateway ──────────────────────────────
    def test_protected_destination_requires_assertion(self):
        """Sin ticket → la VISTA INTERNA protegida responde 403 (la
        protección single-request opera a través del gateway)."""
        res = gateway_post("POST", "/threads/foryou/", token=None, body="{}")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_plaintext_gateway_body_rejected(self):
        """Body en claro al path del gateway con cifrado activo → 400 (la
        política 'cifrado o nada' del middleware también lo cubre)."""
        res = client.post(
            f"/{secrets.token_hex(12)}/",  # path con forma de gateway (24 hex)
            data=json.dumps({"method": "POST", "path": "/threads/foryou/",
                            "query": "", "body": "{}", "nonce": "x"}),
            content_type="application/json",
            HTTP_CLIENT_ASSERTION=token())
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Toggle: flag OFF → gateway deshabilitado (404) ───────────────────
    @override_settings(ENCRYPTED_RESPONSE=False)
    def test_gateway_disabled_when_flag_off(self):
        """Con el cifrado apagado el gateway no opera → 404 IDENTIFICABLE
        (code=GATEWAY_DISABLED): la señal inequívoca para que el FE infiera
        modo normal. Cuerpo plano (flag off → JSONRenderer)."""
        res = gateway_post("POST", "/threads/foryou/", token(), body="{}")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(decode_body(res).get("code"), "GATEWAY_DISABLED")
