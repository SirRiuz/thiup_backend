# Python
import hashlib
import hmac

# Django
from django.conf import settings

# Longitud del token truncado (hex → url-safe). 24 hex = 96 bits. DEBE
# coincidir con el slice del frontend. El patrón comodín de urls.py captura
# exactamente este formato ([0-9a-f]{24}).
TOKEN_LEN = 24


def derive_token(nonce) -> str:
    """
    token = HMAC-SHA256(key=GATEWAY_SEED, msg=nonce) truncado, hex.

    PATH ÚNICO POR REQUEST: el FE genera un nonce aleatorio (CSPRNG) por
    petición, calcula este token y lo usa como path /{token}/ — pura
    GENERACIÓN DE RUIDO. El nonce viaja DENTRO del sobre cifrado (nunca en
    la URL ni en claro); el BA recomputa este token a partir del nonce y lo
    compara con el segmento de la URL.

    OJO: la semilla vive también en el bundle del FE → es OFUSCACIÓN/RUIDO,
    NO un secreto, y NO firma nada crítico (eso es API_SECRET_KEY). Espejo
    EXACTO de la derivación del frontend.
    """
    return hmac.new(
        settings.GATEWAY_SEED.encode(),
        str(nonce).encode(),
        hashlib.sha256,
    ).hexdigest()[:TOKEN_LEN]


def token_matches(token, nonce) -> bool:
    """
    ¿El {token} de la URL corresponde al nonce del sobre? Comparación en
    TIEMPO CONSTANTE (hmac.compare_digest) para no filtrar información por
    timing. El token de URL es RUIDO, NO anti-replay: la protección de
    replay sigue siendo SINGLE_REQUEST_PROTECT (independiente).
    """
    if not isinstance(token, str) or not isinstance(nonce, str) or not nonce:
        return False
    return hmac.compare_digest(token, derive_token(nonce))
