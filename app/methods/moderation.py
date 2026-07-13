# Python
import re

# Django
from django.core.cache import cache
from django.utils import timezone

BLOCKED_TERMS_CACHE_KEY = "moderation:blocked_terms"
# LocMem, same footprint rationale as the tag-activity cache: the blocklist
# changes rarely (admin writes invalidate it immediately via signal), so a
# 5-min TTL keeps the hot search path at zero DB cost.
BLOCKED_TERMS_CACHE_TTL = 300


def blocked_terms() -> list:
    """Active blocklist as normalized terms (lowercase, accent-stripped).

    One tiny indexed query on cache miss; every other call is a LocMem
    lookup — cheap enough for the search/suggest/threads request paths.
    """
    terms = cache.get(BLOCKED_TERMS_CACHE_KEY)
    if terms is None:
        # Local import: this module is used by views and signals; importing
        # the model lazily avoids app-registry order issues.
        from app.models.blocked_term import BlockedTerm

        terms = list(BlockedTerm.objects.filter(is_active=True).values_list("term_norm", flat=True))
        cache.set(BLOCKED_TERMS_CACHE_KEY, terms, BLOCKED_TERMS_CACHE_TTL)
    return terms


def invalidate_blocked_terms() -> None:
    cache.delete(BLOCKED_TERMS_CACHE_KEY)


# Per-process memo of the COMPILED per-term patterns. Compiling inside
# find_blocked_terms leaned on re's internal 512-pattern cache — a large
# seeded blocklist plus the app's other regexes can thrash it, recompiling on
# every search/suggest keystroke. The memo re-syncs whenever the cached terms
# list changes (blocked_terms() already invalidates on admin writes).
_compiled_patterns = None
_compiled_source = None


def _blocked_patterns() -> list:
    global _compiled_patterns, _compiled_source
    terms = blocked_terms()
    if terms != _compiled_source:
        _compiled_patterns = [(term, re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")) for term in terms if term]
        _compiled_source = terms
    return _compiled_patterns


def find_blocked_terms(normalized_text) -> list:
    """Blocked terms present in `normalized_text` as WHOLE words/phrases.

    `normalized_text` must already be normalized with the search pipeline's
    convention (strip_accents + lower) — the same one that derives
    term_norm, so both sides compare equal. Word boundaries on purpose:
    a term must not shadowban an innocent word that merely contains it.
    """
    text = normalized_text or ""
    return [term for term, pattern in _blocked_patterns() if pattern.search(text)]


def shadowban_matching(terms) -> int:
    """Soft-delete every active thread (and tag) carrying any of `terms`.

    Whole-word match on the *_norm columns; the plain __contains prefilter
    lets the GIN trigram indexes narrow the candidates before the regex
    verifies the boundaries. `update_at` is set explicitly (queryset
    .update() skips auto_now) so the purge GC's min-age window counts from
    the shadowban, not from the row's last edit. Returns the number of
    threads deactivated. Rows deactivated here are LATER hard-deleted by
    `purge_inactive` — this is removal, not just hiding.
    """
    from app.models.tag import Tag
    from app.models.thread import Thread

    now = timezone.now()
    banned = 0
    for term in terms:
        # \y = Postgres word boundary (ARE), mirror of the Python check.
        pattern = rf"\y{re.escape(term)}\y"
        banned += Thread.objects.filter(
            is_active=True,
            text_norm__contains=term,
            text_norm__regex=pattern,
        ).update(is_active=False, update_at=now)
        Tag.objects.filter(
            is_active=True,
            name_norm__contains=term,
            name_norm__regex=pattern,
        ).update(is_active=False, update_at=now)
    return banned
