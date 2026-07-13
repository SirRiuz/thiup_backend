# Django
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Clear the LocMem cache before every test.

    The DB is rolled back per test but LocMem state survives the process:
    cached Mask instances (MaskMiddleware), the honeypot blacklist set,
    throttle histories, presence keys and the moderation blocklist would
    otherwise leak between tests — a cached Mask can even point at a row the
    rollback removed, breaking FK inserts in the next test.
    """
    cache.clear()
    yield
