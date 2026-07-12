# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel
from app.utils.text import strip_accents


class BlockedTerm(BaseModel):
    """
    Moderation blocklist entry (shadowban filter).

    A search query carrying any active term (whole-word, case- and
    accent-insensitive) returns the same empty shape as a no-match search —
    to the client, that content simply does not exist — and every thread
    still carrying the term is soft-deleted (`is_active=False`), which the
    `purge_inactive` garbage collector later hard-deletes.

    Matching is done against `term_norm` with the SAME normalization as the
    search pipeline (strip_accents + lower), so 'PEDOFILÍA', 'pedofilia' and
    'Pedofilia' all hit the same entry. Seeded by migration 0020; managed
    from the admin afterwards (saving a term immediately sweeps existing
    content via the post_save signal in app/signals/moderation_signals.py).
    """

    term = models.CharField(
        max_length=100,
        unique=True,
        help_text="Blocked word or phrase, as written by the moderator.",
    )

    # Derived in save() — mirror of Thread.text_norm / Tag.name_norm so the
    # comparison happens with both sides normalized by the same function.
    term_norm = models.CharField(
        max_length=100,
        db_index=True,
        default="",
        blank=True,
        editable=False,
        help_text="Derived: lowercase, accent-stripped term (match key).",
    )

    def save(self, *args, **kwargs):
        # Collapse inner whitespace too: a double space in a phrase would
        # silently never match (text_norm keeps single spaces).
        self.term_norm = " ".join(strip_accents(self.term or "").lower().split())
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.term
