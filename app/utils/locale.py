# Python
import re

# Normalization of the language/region DECLARED by the frontend
# (navigator.language). The backend does NOT infer anything by IP/GeoIP in
# this flow: it only sanitizes what the client sends and persists/filters it.

DEFAULT_LANGUAGE = "es"

_LANGUAGE_RE = re.compile(r"^[a-z]{2,8}$")
_REGION_RE = re.compile(r"^[A-Z0-9]{2,8}$")


def normalize_language(raw, default=DEFAULT_LANGUAGE) -> str:
    """
    "Es " → "es". Odd/missing values → `default` ("es" when creating a
    thread; "" when querying the feed = no language filter).
    """
    clean = (raw or "").strip().lower()
    return clean if _LANGUAGE_RE.match(clean) else default


def normalize_region(raw) -> str:
    """ "co" → "CO". No country in the locale / garbage → "" (no boost)."""
    clean = (raw or "").strip().upper()
    return clean if _REGION_RE.match(clean) else ""
