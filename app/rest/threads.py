# Python
import hashlib
import math
import time
from datetime import timedelta

from django.db.models import Count, F, Q
from django.db.models.query import QuerySet
from django.shortcuts import get_object_or_404

# Django
from django.utils import timezone
from rest_framework import filters
from rest_framework.decorators import action

# Libs
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.status import *
from rest_framework.viewsets import GenericViewSet

from app.constants.search import MAX_QUERY_LENGTH
from app.constants.threads import (
    CLOSEYOU_PROXIMITY_K,
    CLOSEYOU_RADIUS_DEFAULT_KM,
    CLOSEYOU_RADIUS_MAX_KM,
    CLOSEYOU_RADIUS_MIN_KM,
    CLOSEYOU_ROW_FIELDS,
    FORYOU_AFFINITY_K,
    FORYOU_AFFINITY_MAX_TAGS,
    FORYOU_GRACE_HOURS,
    FORYOU_JITTER_BUCKET_SECONDS,
    FORYOU_JITTER_DECAY_HOURS,
    FORYOU_JITTER_RANGE_MAX,
    FORYOU_JITTER_RANGE_MIN,
    FORYOU_MIN_COMMENTERS,
    FORYOU_MIN_REACTORS,
    FORYOU_MIX_POOL,
    FORYOU_REGION_BOOST,
    FORYOU_ROW_FIELDS,
    FORYOU_TAGS_MAX,
)
from app.methods.moderation import find_blocked_terms
from app.methods.threads import attach_top_replies, get_ranked_thread, with_card_relations
from app.models.tag import Tag

# Models
from app.models.thread import Thread
from app.permissions.captcha import human_validator
from app.permissions.client import IsClientAuthenticated
from app.permissions.throttling import TrustedIPScopedRateThrottle
from app.rest.pagination import CustomThreadPagination

# Serailizers
from app.rest.serializers.thread_edit_serializer import (
    ThreadEditHistorySerializer,
    ThreadEditSerializer,
)
from app.rest.serializers.thread_serializer import ThreadSerializer
from app.utils.geo import (
    GEOHASH_PRECISION,
    cells_for_radius,
    normalize_geohash,
)
from app.utils.locale import normalize_language, normalize_region
from app.utils.text import strip_accents


def foryou_threshold(now) -> Q:
    """Entry threshold for the For You feed (constants in
    app/constants/threads.py — momentum SORTS, this decides who ENTERS)."""
    grace_start = now - timedelta(hours=FORYOU_GRACE_HOURS)
    return (
        Q(unique_commenters_count__gte=FORYOU_MIN_COMMENTERS)
        | Q(unique_reactors_count__gte=FORYOU_MIN_REACTORS)
        | Q(create_at__gte=grace_start)
    )


def parse_closeyou_radius(raw) -> float:
    """Clamp the declared radius: 1-100 km; invalid/absent → 15."""
    try:
        radius = float(raw)
    except (TypeError, ValueError):
        return CLOSEYOU_RADIUS_DEFAULT_KM
    return max(CLOSEYOU_RADIUS_MIN_KM, min(CLOSEYOU_RADIUS_MAX_KM, radius))


def foryou_final_momentum(momentum, post_region, user_region, matched_tags) -> float:
    """
    momentum_final = base × region_boost × affinity_boost — computed AT
    SERVE TIME (the boosts depend on the user; the precomputed base
    momentum from the DB is never modified).

    region_boost: ×FORYOU_REGION_BOOST ONLY if post.region == the user's
    region; ×1.0 in any other case (including "UNKNOW").
    affinity_boost: 1 + K × min(matching_tags, cap). Without tags → ×1.0.
    """
    final = momentum
    if user_region and post_region == user_region:
        final *= FORYOU_REGION_BOOST
    if matched_tags:
        final *= 1 + FORYOU_AFFINITY_K * min(matched_tags, FORYOU_AFFINITY_MAX_TAGS)
    return final


def foryou_jitter(thread_id, created_at) -> float:
    """
    Per-candidate ranking jitter — the ONLY source of non-determinism in the
    feed. Deterministic per (thread, time bucket), NOT a fresh random draw
    per call: a plain `random.uniform()` re-rolled on every request broke
    pagination — page 1 and page 2 of the SAME browse are two separate
    requests, and each would re-rank the whole candidate pool differently,
    so a post could appear on both pages or on neither. Hashing (thread_id,
    current FORYOU_JITTER_BUCKET_SECONDS-wide time bucket) keeps the jitter
    IDENTICAL for every request within that window (pagination stays
    consistent — same as scrolling a feed that isn't reshuffling under your
    feet) while still changing between separate visits once the bucket
    rolls over. MD5 (not the builtin `hash()`, randomized per-process via
    PYTHONHASHSEED) so multiple gunicorn workers agree on the same value
    for the same thread — never persisted, never applied to the stored
    momentum_score or the exposed momentum_final.

    The RANGE scales with `created_at`'s age: fresh/unproven content gets
    the wide ±FORYOU_JITTER_RANGE_MAX swing (more mixing → more chances to
    surface), decaying to the narrow ±FORYOU_JITTER_RANGE_MIN past
    FORYOU_JITTER_DECAY_HOURS so already-settled content isn't randomly
    reshuffled for no reason.

    A separate function (not inlined) so tests can patch it to a fixed value
    and get deterministic ordering assertions — see
    `@patch("app.rest.threads.foryou_jitter", return_value=1.0)` in
    app/tests/test_foryou.py.
    """
    age_hours = max((timezone.now() - created_at).total_seconds() / 3600, 0)
    novelty = math.exp(-age_hours / FORYOU_JITTER_DECAY_HOURS)
    jitter_range = FORYOU_JITTER_RANGE_MIN + (FORYOU_JITTER_RANGE_MAX - FORYOU_JITTER_RANGE_MIN) * novelty

    bucket = int(time.time() // FORYOU_JITTER_BUCKET_SECONDS)
    digest = hashlib.md5(f"{thread_id}:{bucket}".encode()).hexdigest()
    unit = int(digest[:8], 16) / 0xFFFFFFFF  # deterministic float in [0, 1)
    return 1 + (unit * 2 - 1) * jitter_range


def rank_foryou_rows(rows, user_region, tag_counts=None) -> list:
    """
    STEP 4 — SORT: momentum_final × jitter DESC (tie-break by date) over the
    BOUNDED set of candidates. Each row is (id, momentum_score,
    create_at, region); tag_counts = {thread_id: matching_tags}.
    """
    counts = tag_counts or {}

    def sort_key(row):
        thread_id, momentum, created, region = row
        final = foryou_final_momentum(momentum, region, user_region, counts.get(thread_id, 0))
        return (final * foryou_jitter(thread_id, created), created)

    return [row[0] for row in sorted(rows, key=sort_key, reverse=True)]


def union_foryou_candidates(tagged_rows, global_rows) -> list:
    """
    STEP 1 — CANDIDATES: union of the global top (discovery) + posts with
    the user's tags (they enter even if their base momentum is low: the
    affinity boost is what lifts them). No duplicates.
    """
    chosen = {row[0]: row for row in tagged_rows}
    for row in global_rows:
        chosen.setdefault(row[0], row)
    return list(chosen.values())


class ThreadsViewSet(GenericViewSet):
    queryset = Thread.objects.filter(is_active=True, visibility=True)
    pagination_class = CustomThreadPagination
    serializer_class = ThreadSerializer
    permission_classes = (IsClientAuthenticated,)
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ("create_at", "reactions_count")
    ordering = ("-create_at",)

    def get_throttles(self) -> list:
        # Entity creation is the anti-abuse companion of the captcha pass.
        # The read-only POST feeds run several queries + in-Python ranking
        # per call, so they get their own (looser) per-IP cap too — a cheap
        # ticket must not let a client hammer them on 0.25 vCPU hardware.
        scope = {
            "create": "threads_create",
            "foryou": "feed",
            "closeyou": "feed",
            "edit": "threads_edit",
        }.get(self.action)
        if scope:
            self.throttle_scope = scope
            return [TrustedIPScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self) -> QuerySet:
        now_date = timezone.localtime(timezone.now())
        queryset = (
            super().get_queryset().filter(Q(expire_date__gte=now_date) | Q(expire_date__isnull=True), is_active=True)
        )

        # reactions_count only exists for ?ordering=(-)reactions_count
        # (trending). Before it was ALWAYS annotated with Count() — a JOIN
        # + GROUP BY against the entire reactions table (1M rows ≈ +1.3s
        # per request) that 95% of requests didn't even use. Now it is an
        # alias of the PRECOMPUTED counter of the For You engine
        # (unique_reactors_count: distinct people, excluding the author —
        # indexed, refreshed every 10 min by recompute_momentum). The
        # serializer does not depend on this annotation: it computes its own
        # reactions_count from the with_card_relations prefetch.
        if "reactions_count" in self.request.GET.get("ordering", ""):
            queryset = queryset.annotate(
                reactions_count=F("unique_reactors_count"),
            )

        if self.action == "retrieve" or self.action == "responses":
            thread_id = self.kwargs.get("pk")
            return queryset.filter(uid=thread_id)

        if self.action == "list":
            query = self.request.GET.get("q")
            tag = self.request.GET.get("tag")
            # Defensa server-side (DoS por cómputo): rechazar q/tag sobre
            # el tope ANTES de filtrar — 400 limpio, sin eco del payload.
            if (query and len(query) > MAX_QUERY_LENGTH) or (tag and len(tag) > MAX_QUERY_LENGTH):
                raise ValidationError({"detail": f"Query exceeds {MAX_QUERY_LENGTH} characters."})
            # with_card_relations: preloads mask/media/reactions and
            # annotates responses_count — eliminates the serializer's N+1 in
            # the lists.
            threads = with_card_relations(
                queryset.filter(visibility=True, is_active=True, is_private=False, sub__isnull=True),
                self.request.mask,
            )

            # Shadowban blocklist: a blocked q/tag answers like a term
            # nobody ever posted about (empty page, same contract). Same
            # whole-word, normalized check as /search/ — read-only here.
            if find_blocked_terms(strip_accents(f"{query or ''} {tag or ''}").lower()):
                return threads.none()

            if tag:
                # EXACT tag (Thread-Tag relation), case- and accent-
                # insensitive: both sides follow the same normalization
                # contract (strip_accents + lower) and compare EQUAL against
                # the derived name_norm column (btree-indexed) — #peru finds
                # #perú. The previous __unaccent__iexact wrapped the column
                # in UPPER(UNACCENT(...)) and seq-scanned Tag per request.
                # distinct(): a post with the same hashtag repeated creates
                # several Tag rows and the join would duplicate the thread.
                normalized_tag = strip_accents(tag).lower()
                return threads.filter(tag__name_norm=normalized_tag).distinct().order_by("-create_at")

            if query:
                # Same contract as /search/: plain __contains against
                # text_norm (query normalized with the SAME function that
                # writes the column) so the GIN trigram index serves the
                # match — text__unaccent__icontains seq-scanned the table.
                normalized_query = strip_accents(query).lower()
                return threads.filter(text_norm__contains=normalized_query).order_by("-create_at")

            return threads.order_by("-create_at")

        return queryset

    def list(self, request) -> Response:
        """
        Gets a paged list of threads
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        Request params:

                tag - Winter the threads by hashtag
                query - Winter the threads by query param
        ---
        response code: 200
        ---
        Response Body:

                {
                    "count": 0,
                    "next": "",
                    "previous": "",
                    "results": [
                        {
                            "id": "...",
                            "create_at": "4 days",
                            "content": "...",
                            "text": "...",
                            "visibility": true,
                            "expire_date": null,
                            "sub": null,
                            "mask": {
                                "id": "...",
                                "hash": "..."
                            },
                            "parent": null,
                            "responses_count": 3,
                            "uid": "a1b2c3d4e5f6",
                            "is_expired": null,
                            "media": [],
                            "last_reaction": null,
                            "reactions": [
                                {
                                    "id": "...",
                                    "name": "...",
                                    "emoji": "...",
                                    "reaction_count": 1
                                }
                            ],
                            "is_new": false
                        },
                        ...
                    ]
                }

        Response codes:

            201 - Obtains a list of threads.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        queryset = self.filter_queryset(self.get_queryset())
        pages = self.paginate_queryset(queryset)
        serializer = self.get_serializer(pages, many=True, context=({"mask": request.mask, "short": True}))
        cards = serializer.data
        # Conversation preview — every feed of root cards carries it.
        attach_top_replies(cards, pages, request.mask)

        return self.get_paginated_response(({"data": cards}))

    @action(detail=False, methods=["GET"])
    def mine(self, request) -> Response:
        """
        GET /threads/mine/ — the threads created by the AUTHENTICATED user.

        STRICTLY scoped server-side to request.mask (the user's identity in
        this app): never returns other people's threads. Includes the
        user's own hidden/expired threads (they are theirs). Paginated, most
        recent first, same serializer/shape as the feed (same mapper in the
        frontend) and the same anti-N+1 preloads.
        """
        if getattr(request, "mask", None) is None:
            return Response({"detail": "No mask."}, status=HTTP_404_NOT_FOUND)

        threads = with_card_relations(
            Thread.objects.filter(
                is_active=True,
                sub__isnull=True,
                mask=request.mask,
            ),
            request.mask,
        ).order_by("-create_at")

        pages = self.paginate_queryset(threads)
        serializer = self.get_serializer(pages, many=True, context=({"mask": request.mask, "short": True}))
        cards = serializer.data
        # Conversation preview — the user's own threads carry it too.
        attach_top_replies(cards, pages, request.mask)

        return self.get_paginated_response(({"data": cards}))

    def retrieve(self, request, pk) -> Response:
        """
        Retrieve the infromation of the thread
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 200
        ---
        Response Body:

                {
                    "id": "...",
                    "create_at": "4 days",
                    "content": "...",
                    "text": "...",
                    "visibility": true,
                    "expire_date": null,
                    "sub": null,
                    "mask": {
                        "id": "...",
                        "hash": "..."
                    },
                    "parent": null,
                    "responses_count": 3,
                    "uid": "a1b2c3d4e5f6",
                    "is_expired": null,
                    "media": [],
                    "last_reaction": null,
                    "reactions": [
                        {
                            "id": "...",
                            "name": "s",
                            "emoji": "...",
                            "reaction_count": 1
                        }
                    ],
                    "is_new": false
                }

        Response codes:

            201 - Obtains an object of a thread.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        # with_card_relations: retrieve serializes on the serializer's fast
        # path (prefetches/annotation) instead of the ~4-query legacy fallback.
        thread = get_object_or_404(with_card_relations(self.get_queryset(), request.mask))
        serializer = self.get_serializer(thread, many=False, context=({"mask": request.mask})).data

        return Response(serializer, status=HTTP_200_OK)

    @human_validator
    def create(self, request) -> Response:
        """
        Create a new thread
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 200
        ---
        Request body:

                {
                    "media": [
                        {
                            "data": "...",
                            "is_video": "...",
                            "resolution": "..."
                        },
                        ...
                    ],
                    "content": {...},
                    "text": "...",
                    "visibility": false,
                    "expire_date": null,
                    "sub": null,
                    "mask": null
                }

        Response Body:

                {
                    "id": "...",
                    "create_at": "a moment",
                    "content": {
                        ...
                    },
                    "text": "kasndads",
                    "visibility": false,
                    "expire_date": null,
                    "sub": null,
                    "mask": null,
                    "parent": null,
                    "responses_count": 0,
                    "uid": "a1b2c3d4e5f6",
                    "is_expired": null,
                    "media": [],
                    "last_reaction": null,
                    "reactions": [],
                    "responses": [],
                    "is_new": true
                }

        Response codes:

            201 - Obtains an object of the created thread.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        sub_tread = request.data.get("sub")
        if sub_tread:
            # Only allow replying to a LIVE, visible thread — mirrors the
            # is_active/visibility filter used by reactions/reports; without
            # it a reply could be attached to a soft-deleted or hidden thread.
            parent = get_object_or_404(Thread.objects.filter(uid=sub_tread, is_active=True, visibility=True))
            # Owner turned commenting off — reject BEFORE validating/creating
            # anything else. "replies_disabled" as the literal detail string
            # (not a sentence) is what the frontend matches on to show its
            # own dedicated copy instead of a generic post-failed message.
            if parent.replies_disabled:
                raise ValidationError({"detail": "replies_disabled"})
            sub_tread = parent.uid

        # No show_responses: a freshly created thread has no replies, so the
        # old `responses: []` echo only cost an extra query — and the
        # frontend never read it.
        serializer = self.get_serializer(data=request.data, context=({"mask": request.mask}))
        serializer.is_valid(raise_exception=True)

        # "Snap": self-deletes 24h after creation. The client only OPTS IN
        # (is_snap=True) — the expiry itself is computed here, server-side,
        # to a FIXED 24h window; a client can never set an arbitrary
        # expire_date. Deliberately never exposed on ThreadEditSerializer,
        # so this is also the ONLY place it's ever set — immutable by
        # construction, not by a guard that could be forgotten elsewhere.
        is_snap = bool(request.data.get("is_snap", False))
        expire_date = timezone.now() + timedelta(hours=24) if is_snap else None

        # Language/region DECLARED by the FE (navigator.language) — the
        # backend only normalizes and persists, no GeoIP. language feeds
        # the For You language filter (legacy/absent → "es"); region the
        # regional boost ("" if the locale carries no country).
        serializer.save(
            language=normalize_language(request.data.get("language")),
            region=normalize_region(request.data.get("region")),
            # geohash: ONLY if the author enabled location sharing
            # (opt-in). It arrives already COARSE (precision 5) and fuzzed
            # by the client — the backend never sees coordinates. Null → the
            # post is not geolocatable (does not appear in Close You).
            geohash=normalize_geohash(request.data.get("geohash")),
            is_private=bool(request.data.get("is_private", False)),
            is_snap=is_snap,
            expire_date=expire_date,
            replies_disabled=bool(request.data.get("replies_disabled", False)),
        )
        return Response(serializer.data, status=HTTP_201_CREATED)

    @action(detail=True, methods=["POST"])
    def edit(self, request, pk=None) -> Response:
        """
        Edit the text and/or media of a thread OR reply the caller owns.
        ---
        Request body:

                {
                    "text": "new text",
                    "content": {...},
                    "remove_media": ["<file uid>", ...],
                    "add_media": ["<file uid>", ...]
                }

        `remove_media`/`add_media` are ThreadFile uids — `remove_media` must
        already be attached to THIS thread and owned by the caller's mask
        (hard-deleted, same storage cleanup as any other ThreadFile removal);
        `add_media` must already be confirmed (via the existing
        POST /thread-files/confirm/) against THIS thread and owned by the
        caller's mask — this endpoint does not attach new files itself, it
        only counts the ones already attached for the edit-history note.

        Ownership uses the SAME opaque-404 pattern as every other mutation in
        this API: a thread that isn't active, or isn't owned by the caller's
        mask, 404s — never 403 (this codebase never reveals existence to a
        non-owner).

        Response codes:

            200 - Returns the updated thread/reply (same shape as retrieve).
            400 - Invalid/empty text.
            404 - Not found, inactive, or not owned by the caller.
            429 - Rate limited.
        """
        thread = get_object_or_404(Thread, uid=pk, is_active=True, mask=request.mask)

        serializer = ThreadEditSerializer(data=request.data, instance=thread, context={"mask": request.mask})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        out = ThreadSerializer(updated, context={"mask": request.mask})
        return Response(out.data, status=HTTP_200_OK)

    @action(detail=True, methods=["GET"], url_path="edit-history")
    def edit_history(self, request, pk=None) -> Response:
        """
        Text-only revision history of a thread/reply, newest first. PUBLIC to
        every viewer (deliberately NOT owner-gated) — anyone can see how a
        post's text changed over time. Media changes are represented only as
        added/removed counts, never previews (see ThreadEditHistory).

        Response codes:

            200 - Paginated list of past revisions.
            404 - Thread not found or inactive.
        """
        thread = get_object_or_404(Thread, uid=pk, is_active=True)
        history = thread.edit_history.filter(is_active=True).order_by("-create_at")
        page = self.paginate_queryset(history)
        serializer = ThreadEditHistorySerializer(page, many=True)
        return self.get_paginated_response({"data": serializer.data})

    @action(detail=False, methods=["POST"])
    def foryou(self, request) -> Response:
        """
        For You feed (POST): the feed params travel in the BODY — the tags
        reveal interests and in GET they ended up in the access logs of
        nginx/proxies (the URL is logged; the body is not). The page goes as
        a short query param (?page=N, not sensitive): this way DRF
        pagination works unchanged.

        Body: { "lang": "es", "region": "CO", "tags": ["a","b"] | "a,b" }
        — all optional; empty body → cold start (global top).

        Ranking identical to before (threshold + precomputed momentum +
        affinity_boost + region_boost + language filter): only the
        TRANSPORT changed. Same response contract as GET /threads/
        (count/next/previous/results — HomeFeedCard does not change).

        Response codes:

            200 - Obtains the For You page of threads.
            401 - The client is not authorized.
            500 - An error occurred on the server.
        """
        # ID-FIRST strategy (key to performance with large tables): first
        # cheap sub-queries that bring back ONLY ids already sorted by
        # momentum (index), and the rich queryset (with_card_relations,
        # anti-N+1) is materialized ONLY for the ids of the served page. The
        # full rich queryset is never passed to the paginator — that cost
        # ~1.3s per request (JOIN + GROUP BY + sort of 19k posts × 1M
        # reactions before the LIMIT).
        threshold = foryou_threshold(timezone.now())
        body = request.data if isinstance(request.data, dict) else {}
        tags = self.__parse_foryou_tags(body)
        # Region declared by the FE (navigator.language) → ×1.5 boost on
        # posts of the same region, per query. No GeoIP.
        region = normalize_region(body.get("region", ""))
        # Declared language → HARD FILTER (with fallback). No parameter →
        # default "" = no filter (old clients / cold global).
        lang = normalize_language(body.get("lang", ""), default="")
        return self.__foryou_response(request, threshold, tags, region, lang)

    @action(detail=False, methods=["POST"])
    def closeyou(self, request) -> Response:
        """
        Close You (near me, ~10 km): SAME ENGINE as For You (threshold +
        grace + precomputed momentum + POST pagination + serializer —
        __feed_querysets/__serve_feed_page); only the filter (geohash
        cells) and the boost (proximity) change.

        Body: { "geohash": "d2g6e", "radius_km": 15 }
          · geohash: the READER's cell (precision 5, already FUZZED on the
            client). EPHEMERAL: lives for this request and is discarded.
          · radius_km: feed radius, clamp 1-100 (default 15). The cell grid
            is generated here with ADAPTIVE PRECISION — ≤25 km at
            precision 5 (geohash IN, index), >25 km at precision 4
            (geohash4 IN, index) — cells ALWAYS bounded (≤169).

        momentum_final = momentum_base × proximity_boost, with a boost that
        DECREASES per ring: 1 + K×(1−ring/n) → your cell ×1.35, edge of the
        radius ×1.0 (NO affinity or region). The SORT itself additionally
        multiplies by foryou_jitter() (transient, never persisted, never
        part of momentum_final) — same as For You. Fallback: newest LOCAL of
        the area (never posts from somewhere else in the world).

        Response codes:

            200 - Obtains the Close You page of threads.
            400 - Missing/invalid reader geohash.
            401 - The client is not authorized.
        """
        body = request.data if isinstance(request.data, dict) else {}
        # Compat: clients that still send cells[] → the first is the center.
        raw_center = body.get("geohash") or (
            (body.get("cells") or [None])[0] if isinstance(body.get("cells"), (list, tuple)) else None
        )
        center = normalize_geohash(raw_center)
        if not center:
            return Response({"detail": "geohash (reader cell) required."}, status=HTTP_400_BAD_REQUEST)

        radius_km = parse_closeyou_radius(body.get("radius_km"))
        ring_by_cell, precision = cells_for_radius(center, radius_km)

        visible, qualifying = self.__feed_querysets()
        order = ("-momentum_score", "-create_at")

        # Indexed filter according to the adaptive precision.
        cells = list(ring_by_cell)
        if precision == GEOHASH_PRECISION:
            area_filter = Q(geohash__in=cells)
        else:
            area_filter = Q(geohash4__in=cells)

        rows = list(qualifying.filter(area_filter).order_by(*order).values_list(*CLOSEYOU_ROW_FIELDS)[:FORYOU_MIX_POOL])

        # Decreasing proximity boost: post → cell → normalized distance
        # (0=center, 1=edge) → boost, all in memory O(candidates), no N+1.
        def proximity_final(momentum, geohash) -> float:
            key = geohash if precision == GEOHASH_PRECISION else (geohash or "")[: GEOHASH_PRECISION - 1]
            ring = ring_by_cell.get(key, 1.0)
            return momentum * (1 + CLOSEYOU_PROXIMITY_K * (1 - ring))

        ordered_ids = [
            row[0]
            for row in sorted(
                rows,
                key=lambda r: (proximity_final(r[1], r[3]) * foryou_jitter(r[0], r[2]), r[2]),
                reverse=True,
            )
        ]

        # Shared engine — newest LOCAL filler (geolocated posts of the
        # area, no threshold): the feed is not left empty by the threshold,
        # but NEVER fills with posts from somewhere else in the world.
        return self.__serve_feed_page(
            request,
            ordered_ids,
            filler_qs=visible.filter(area_filter),
            momentum_final_fn=lambda t: proximity_final(t.momentum_score, t.geohash),
        )

    def __parse_foryou_tags(self, body) -> list:
        """
        Normalizes the tags from the BODY — accepts a JSON list (["a","b"])
        or a comma-separated string ("a,b") — same as how Tags are stored
        (lowercase, no '#', no accents — name_norm). Defensive truncation to
        FORYOU_TAGS_MAX (query cost). The tags are EPHEMERAL: they live in
        this request and are discarded — they are never persisted nor
        associated with the request's mask.
        """
        raw = body.get("tags", "")
        pieces = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
        tags = []
        for piece in pieces:
            clean = strip_accents(str(piece).strip().lstrip("#").lower())
            if clean and clean not in tags:
                tags.append(clean)
        return tags[:FORYOU_TAGS_MAX]

    # ── SHARED FEED ENGINE (For You + Close You) ────────────────────────
    # The "engine" is the pieces that are identical in both feeds: visible
    # universe + threshold/grace (__feed_querysets) and filler + pagination
    # + rich queryset + momentum_final + serializer (__serve_feed_page).
    # Each feed only contributes its candidate FILTER and its boost function.

    def __feed_querysets(self) -> tuple:
        """(visible, qualifying): the feed universe + entry threshold with
        grace window — identical for For You and Close You."""
        now_date = timezone.localtime(timezone.now())
        visible = Thread.objects.filter(
            Q(expire_date__gte=now_date) | Q(expire_date__isnull=True),
            is_active=True,
            visibility=True,
            is_private=False,
            sub__isnull=True,
        )
        return visible, visible.filter(foryou_threshold(timezone.now()))

    def __serve_feed_page(self, request, ordered_ids, filler_qs, momentum_final_fn) -> Response:
        """
        Shared serving engine: newest FALLBACK (over filler_qs — global in
        For You, local in Close You), pagination of the id list, rich
        queryset (with_card_relations, no N+1) ONLY for the page,
        momentum_final per query and the card serializer. Each card may carry
        a `top_reply` conversation preview (see __attach_top_replies).
        """
        page_size = self.paginator.get_page_size(request)
        if len(ordered_ids) < page_size and filler_qs is not None:
            seen = set(ordered_ids)
            filler = filler_qs.order_by("-create_at").values_list("id", flat=True)[:FORYOU_MIX_POOL]
            ordered_ids += [i for i in filler if i not in seen]

        page_ids = self.paginate_queryset(ordered_ids)
        threads = with_card_relations(Thread.objects.filter(id__in=page_ids), request.mask)
        by_id = {t.id: t for t in threads}
        page = [by_id[i] for i in page_ids if i in by_id]

        # momentum_final = the SAME formula that ordered this feed,
        # computed per query — the DB's base momentum never changes.
        for thread in page:
            thread.momentum_final = momentum_final_fn(thread)

        serializer = self.get_serializer(page, many=True, context=({"mask": request.mask, "short": True}))
        cards = serializer.data
        # Conversation preview — shared with every feed surface (see
        # app/methods/threads.py::attach_top_replies).
        attach_top_replies(cards, page, request.mask)

        return self.get_paginated_response(({"data": cards}))

    def __foryou_response(self, request, threshold, tags, region, lang) -> Response:
        """
        Builds the For You — global or personalized — with the id-first
        technique: sub-queries of ONLY lightweight tuples ordered by base
        momentum (composite momentum/create_at index), composition and
        re-rank in Python, and the rich queryset only for the current page.

        STEP 2 — HARD language filter (?lang=, declared by the FE from
        navigator.language): candidates only from the reader's language; if
        after the filter not even one page is filled → FALLBACK: the filter
        is removed and the global top is served (the feed is never empty).

        Without tags → candidates = global top (cold start). With tags →
        candidates = union of global top + posts with your tags. In BOTH
        cases the final order is momentum_final DESC (STEP 4): base momentum
        × boosts AT QUERY TIME. No duplicates.
        """
        visible, base_qualifying = self.__feed_querysets()
        order = ("-momentum_score", "-create_at")

        def build_candidates(qualifying) -> tuple:
            # Global candidates (discovery / cold start): top-N by index.
            # The tuples (id, momentum, create_at, region) allow boost +
            # re-rank of the union in Python without more queries. Note:
            # ENTRY to the pool is by base momentum; the boost reorders
            # within the pool (deliberate approximation — exact for every
            # post in the top-500, cheap for the Raspberry).
            global_rows = list(qualifying.order_by(*order).values_list(*FORYOU_ROW_FIELDS)[:FORYOU_MIX_POOL])

            if not tags:
                return rank_foryou_rows(global_rows, region), {}

            # Posts with the user's tags — they enter the pool even if their
            # base momentum is low (the affinity boost lifts them). The join
            # with Tag duplicates rows if a post matches several tags: it is
            # deduplicated in Python (dict.fromkeys preserves order) instead
            # of DISTINCT, which in Postgres clashes with the ORDER BY of
            # non-selected columns.
            tagged_raw = (
                qualifying.filter(tag__name_norm__in=tags)
                .order_by(*order)
                .values_list(*FORYOU_ROW_FIELDS)[: FORYOU_MIX_POOL * 2]
            )
            tagged_rows = list(dict.fromkeys(tagged_raw))[:FORYOU_MIX_POOL]
            candidate_rows = union_foryou_candidates(tagged_rows, global_rows)

            # matching_tags per candidate: ONE bounded query (only the pool
            # ids), COUNT(DISTINCT name_norm) — no N+1. Ephemeral: lives only
            # as long as the request.
            tag_counts = dict(
                Tag.objects.filter(
                    thread_id__in=[row[0] for row in candidate_rows],
                    name_norm__in=tags,
                )
                .values("thread_id")
                .annotate(c=Count("name_norm", distinct=True))
                .values_list("thread_id", "c")
            )
            return rank_foryou_rows(candidate_rows, region, tag_counts), tag_counts

        # STEP 2: hard filter by language (indexed language) if the FE
        # declared it; without ?lang= it does not filter (old clients).
        qualifying = base_qualifying.filter(language=lang) if lang else base_qualifying
        ordered_ids, tag_counts = build_candidates(qualifying)
        page_size = self.paginator.get_page_size(request)

        # Language FALLBACK: very few posts in the reader's language →
        # remove the filter and serve the global top (it only costs extra
        # queries in this rare case, not on the happy path).
        if lang and len(ordered_ids) < page_size:
            ordered_ids, tag_counts = build_candidates(base_qualifying)

        # Shared engine: newest GLOBAL filler + pagination + rich page +
        # momentum_final (the For You formula) + serializer.
        return self.__serve_feed_page(
            request,
            ordered_ids,
            filler_qs=visible,
            momentum_final_fn=lambda t: foryou_final_momentum(
                t.momentum_score, t.region, region, tag_counts.get(t.id, 0)
            ),
        )

    @action(detail=True, methods=["GET"])
    def responses(self, request, pk) -> Response:
        """
        Get responses of the thread
        ---
        Content/Type:
            application/json
        ---
        Header Parameters:
            token: Auth token
        ---
        response code: 200
        ---
        Response Body:

                {
                    "head": {
                        "id": "...",
                        "content": "...",
                        "create_at": "11 days",
                        "text": "...",
                        "visibility": true,
                        "expire_date": null,
                        "sub": null,
                        "mask": {
                            "id": "...",
                            "is_active": true,
                            "hash": "...",
                            "country_code": "Unknow"
                        },
                        "parent": null,
                        "responses_count": 13,
                        "uid": "a1b2c3d4e5f6",
                        "is_expired": null,
                        "media": [],
                        "last_reaction": null,
                        "reactions": [],
                        "is_new": false
                    },
                    "parents": [],
                    "count": 13,
                    "next": null,
                    "previous": "...",
                    "results": [
                        {
                            "id": "...",
                            "content": "...",
                            "create_at": "10 days",
                            "text": "low",
                            "visibility": true,
                            "expire_date": null,
                            "sub": "...",
                            "mask": {
                                "id": "...",
                                "is_active": true,
                                "hash": "...",
                                "country_code": "Unknow"
                            },
                            "parent": "...",
                            "responses_count": 0,
                            "uid": "...",
                            "is_expired": null,
                            "media": [],
                            "responses": [],
                            "last_reaction": null,
                            "reactions": [],
                            "is_new": false
                        },
                        ...
                    ]
                }

        Response codes:

            200 - Returns a list with the responses of a thread.
            401 - The client is not authorized.
            500 - An error occurred on the server.

        Note: `pk` can be ANY thread uid — a root thread or a reply at any
        depth (a reply IS a Thread with `sub` set). When the head is a reply,
        `parents` carries its ancestor chain (root first, immediate parent
        last) so the client can render the Threads-style comment permalink
        with full thread continuity. For a root thread `parents` is [].
        """
        # with_card_relations: the head card serializes on the fast path too.
        thread = get_object_or_404(with_card_relations(self.get_queryset(), request.mask))

        # ── Ancestor chain (comment permalink) ─────────────────────────────
        # Walk the `sub` FK up to the root with the SAME liveness rules as the
        # head itself (active + visible + not expired): a hidden/expired
        # ancestor TRUNCATES the chain there — the permalink still resolves,
        # it just shows less context. Cheap id-only walk (one indexed PK
        # lookup per level; real depth is small), then ONE with_card_relations
        # batch so each ancestor card serializes on the fast path.
        now_date = timezone.localtime(timezone.now())
        alive = Q(expire_date__gte=now_date) | Q(expire_date__isnull=True)
        chain_ids = []
        seen_ids = {thread.id}
        node_id = thread.sub_id
        while node_id and node_id not in seen_ids:
            row = (
                Thread.objects.filter(alive, pk=node_id, is_active=True, visibility=True).values("id", "sub_id").first()
            )
            if row is None:
                break
            chain_ids.append(row["id"])
            seen_ids.add(row["id"])
            node_id = row["sub_id"]
        chain_ids.reverse()  # root → immediate parent

        parents = []
        if chain_ids:
            cards = with_card_relations(Thread.objects.filter(pk__in=chain_ids), request.mask)
            by_id = {t.id: t for t in cards}
            parents = [by_id[pk] for pk in chain_ids if pk in by_id]

        # op_mask = the ROOT author's mask (chain root when the head is a
        # reply, the head itself otherwise) so is_op means "authored by the
        # thread's Original Poster" all along the permalink — head, ancestors
        # and replies alike. Same boolean-only, thread-local privacy contract.
        op_mask = parents[0].mask if parents else thread.mask

        head_serializer = self.get_serializer(thread, many=False, context=({"mask": request.mask, "op_mask": op_mask}))
        parents_serializer = self.get_serializer(
            parents, many=True, context=({"mask": request.mask, "op_mask": op_mask})
        )

        responses = with_card_relations(
            get_ranked_thread().filter(is_active=True, sub=thread),
            request.mask,
        )

        # Reply ordering (recent/top/oldest), same contract as /home-new
        # (?ordering=...). The ranked queryset exposes `reaction_count` (not
        # `reactions_count`), so we map the alias the frontend sends.
        # Without ordering => legacy ranking (-index): does not alter /t/:thread.
        replies_ordering = {
            "-create_at": ("-create_at",),
            "create_at": ("create_at",),
            "-reactions_count": ("-reaction_count", "-create_at"),
        }.get(request.query_params.get("ordering"), ("-index",))
        responses = responses.order_by(*replies_ordering)

        pages = self.paginate_queryset(responses)
        # op_mask = the chain root author's mask (see above), so each reply can
        # compute is_op (reply author == OP) LOCALLY to this thread, as a
        # boolean only.
        serializer = self.get_serializer(
            pages, many=True, context=({"mask": request.mask, "op_mask": op_mask, "show_responses": True})
        )

        return self.get_paginated_response(
            ({"data": serializer.data, "context": {"head": head_serializer.data, "parents": parents_serializer.data}})
        )
