# Django
from django.contrib.postgres.indexes import GinIndex
from django.db import models

# Libs
from app.models.base_model import BaseModel

# Models
from app.models.mask import Mask
from app.utils.text import strip_accents


class Thread(BaseModel):
    content = models.JSONField("Content of the thread")
    text = models.TextField(help_text="Text of the thread.")
    # Normalized text (lowercase, no accents) — same convention as
    # Tag.name_norm. It is the ONLY column the text search matches against:
    # search normalizes the query the same way (strip_accents + lower) and a
    # GIN pg_trgm index over this column serves the infix LIKE. Matching the
    # raw `text` with __unaccent__icontains generated
    # UPPER(UNACCENT(text)) LIKE ..., an expression no index could serve
    # (measured: Seq Scan over the whole table on every search/count).
    text_norm = models.TextField(
        default="", blank=True, editable=False, help_text="Derived: lowercase, accent-stripped text (search index)."
    )
    visibility = models.BooleanField(default=True, help_text="This thread can be indexed by the feed.")

    expire_date = models.DateTimeField(null=True, blank=True, help_text="Add an expiration date to the thread")

    sub = models.ForeignKey(to="self", on_delete=models.CASCADE, null=True, blank=True, help_text="Parent thread")

    mask = models.ForeignKey(to=Mask, on_delete=models.CASCADE, null=True)

    # Thread language — DECLARED by the frontend at creation time
    # (navigator.language → "es"), WITHOUT GeoIP/IP. It's the WHERE of the
    # For You hard language filter (hence db_index). Legacy without language →
    # default "es" (existing content is in Spanish; this way they aren't
    # excluded from the feed).
    language = models.CharField(
        max_length=8,
        default="es",
        db_index=True,
        help_text="Creator's language from navigator.language (For You filter).",
    )

    # Thread region — the country code from the creator's locale
    # (navigator.language → "CO"), also declared by the frontend, WITHOUT
    # GeoIP. Used by the For You regional boost: if it matches the reader's
    # region, momentum_final = momentum_score × 1.5 AT QUERY TIME — the
    # precomputed momentum_score (base) is NEVER modified.
    region = models.CharField(
        max_length=50,
        default="",
        blank=True,
        help_text="Creator's country code from navigator.language (For You boost).",
    )

    # Thread geohash (precision 5 ≈ ~5 km cell) — ONLY if the author
    # enabled location sharing (opt-in). Computed by the CLIENT with
    # already-fuzzed coords: the backend never sees coordinates, and never
    # stores more precision than the cell. Feeds the Close You feed
    # (~10 km = reader's cell + 8 neighbors). Null → not geolocatable.
    geohash = models.CharField(
        max_length=12,
        null=True,
        blank=True,
        db_index=True,
        help_text="Author's coarse geohash cell, opt-in (Close You feed).",
    )

    # Precision-4 geohash prefix (~39 km/cell), ALWAYS derived in
    # save() — feeds the indexed Close You filter with large radii
    # (>25 km) without LEFT(geohash,4) per row (full scan). COARSER
    # than geohash: exposes nothing new.
    geohash4 = models.CharField(
        max_length=4,
        null=True,
        blank=True,
        db_index=True,
        help_text="Derived 4-char geohash prefix (Close You wide radius).",
    )

    # ── Momentum engine (For You) ────────────────────────────────────────
    # Precomputed by `manage.py recompute_momentum` (cron every 10 min).
    # The For You feed NEVER computes the formula per request: it only reads
    # these indexed fields (WHERE on the counters + ORDER BY momentum_score).
    #
    # Freshness-first, engagement-modulated: freshness is the ONLY age-based
    # term (a brand-new post ranks on arrival, no engagement required);
    # engagement multiplies on top, log-scaled (diminishing returns).
    #
    #   points = unique_reactors + unique_commenters*3
    #          + commenters_replied_by_author*5 [+ log10(views+1)*2 — no
    #            views counter in v1, term omitted]
    #   freshness = e^(-age_hours / MOMENTUM_FRESH_TAU_HOURS)   # TAU=8h
    #   engagement_boost = MOMENTUM_ENGAGE_K * ln(1 + points)   # K=0.6
    #   momentum_score = freshness * (1 + engagement_boost)
    #
    # Golden rule: each signal counts DISTINCT MASKS and excludes the author.
    momentum_score = models.FloatField(
        default=0, db_index=True, help_text="Precomputed momentum score (For You ordering)."
    )

    unique_reactors_count = models.PositiveIntegerField(
        default=0, db_index=True, help_text="Distinct masks that reacted, excluding the author."
    )

    unique_commenters_count = models.PositiveIntegerField(
        default=0, db_index=True, help_text="Distinct masks that commented, excluding the author."
    )

    # Set explicitly by the edit endpoint (app/rest/threads.py: edit), never
    # derived in save() — a future unrelated write must never silently stamp
    # it. NULL means never edited. Distinct from `update_at` (auto_now=True):
    # that column is also touched by unrelated writes (e.g. moderation
    # shadowban), so it cannot double as the "this content was edited" signal.
    edited_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        help_text="Timestamp of the last content edit (text/media). NULL if never edited.",
    )

    # Excluded from feed/search (list, foryou/closeyou, search) but NEVER
    # from retrieve/responses — a private thread stays fully viewable by
    # anyone who has its direct /p/:uid link, it just doesn't surface
    # anywhere discoverable. Toggleable anytime via the edit endpoint
    # (app/rest/serializers/thread_edit_serializer.py).
    is_private = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Excluded from feed/search — viewable only via direct URL.",
    )

    # A "snap" thread self-deletes 24h after creation: set ONLY at creation
    # time (app/rest/threads.py: create), by computing expire_date = now +
    # 24h in the same call — never exposed as a writable ThreadEditSerializer
    # field, so it can never change after posting (see that serializer's
    # closed field list). Unlike is_private, a snap thread stays indexable
    # while it's alive (it doesn't touch visibility/is_private at all) — its
    # disappearance is driven purely by the existing expire_date filtering
    # every read path already applies, plus purge_inactive's GC sweep.
    is_snap = models.BooleanField(
        default=False,
        help_text="Self-deletes 24h after creation (via expire_date). Set only at creation, never editable.",
    )

    # Owner-controlled: reject new replies while True. Enforced server-side
    # in app/rest/threads.py's create() (the real boundary) — the frontend
    # hiding the reply box is only a courtesy, not the security check.
    # Toggleable anytime via the edit endpoint, same as is_private.
    replies_disabled = models.BooleanField(
        default=False,
        help_text="Owner turned off commenting on this thread.",
    )

    def save(self, *args, **kwargs):
        # Canonicalize language/region for ANY writer — including admin
        # (editing "ES"/"co" by hand would break the For You filter, which
        # compares exact and indexed: language='es'). The default for
        # language is "es" (same as legacy).
        from app.utils.locale import normalize_language, normalize_region

        self.language = normalize_language(self.language)
        self.region = normalize_region(self.region)
        # geohash4 is ALWAYS derived from geohash (never written directly):
        # impossible for them to get out of sync, no matter where the write comes from.
        self.geohash4 = self.geohash[:4] if self.geohash else None
        # text_norm is ALWAYS derived from text (same rule as geohash4).
        self.text_norm = strip_accents(self.text or "").lower()
        super().save(*args, **kwargs)

    class Meta:
        # All listings order by create_at (feed/search/tag): a descending
        # index so that the pagination's ORDER BY + LIMIT is cheap.
        # The composite momentum/create_at covers the exact ORDER BY of For
        # You (top-N by momentum with date as tiebreaker): the planner
        # walks the index in order and cuts at the LIMIT, with no sort.
        indexes = [
            models.Index(fields=["-create_at"], name="thread_create_at_desc_idx"),
            models.Index(
                fields=["-momentum_score", "-create_at"],
                name="thread_momentum_desc_idx",
            ),
            # GIN trigram sobre text_norm → la búsqueda y el autocomplete
            # (text_norm__contains, ambos lados ya normalizados) usan índice,
            # no seqscan. Reemplaza al índice sobre `text` crudo, que NUNCA
            # se usaba (idx_scan=0): __unaccent__icontains envuelve la
            # columna en UPPER(UNACCENT(...)) y el planner no puede casar esa
            # expresión con un índice sobre la columna sin funciones.
            GinIndex(
                name="thread_text_norm_trgm_idx",
                fields=["text_norm"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self) -> str:
        # uid + start of the text: readable in the admin and in FKs.
        snippet = (self.text or "")[:40]
        return f"{self.uid} · {snippet}"
