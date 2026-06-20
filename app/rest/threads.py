# Python
from datetime import timedelta

# Django
from django.utils import timezone
from django.db.models import Q, F, Count
from django.shortcuts import get_object_or_404
from django.db.models.query import QuerySet
from rest_framework import filters
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.status import *

# Models
from app.models.thread import Thread
from app.models.tag import Tag

# Serailizers
from app.rest.serializers.thread_serializer import \
    ThreadSerializer

# Libs
from rest_framework.exceptions import ValidationError
from app.permissions.client import IsClientAuthenticated
from app.utils.text import strip_accents
from app.constants.search import MAX_QUERY_LENGTH
from app.utils.locale import normalize_language, normalize_region
from app.utils.geo import (
    normalize_geohash,
    cells_for_radius,
    GEOHASH_PRECISION,
)
from app.rest.pagination import CustomThreadPagination
from app.methods.threads import get_ranked_thread, with_card_relations


# ── For You entry threshold ──────────────────────────────────────────────
# SEPARATE from momentum: momentum SORTS, the threshold decides who ENTERS.
# A post enters if it meets AT LEAST ONE:
#   unique_commenters ≥ 1  OR  unique_reactions ≥ 3  OR  age < 2h
# (the grace window lets new posts receive their first interactions). All
# fields are precomputed by `recompute_momentum`: the WHERE is over indexed
# counters, recomputing nothing per request.
FORYOU_MIN_COMMENTERS = 1
FORYOU_MIN_REACTORS = 3
FORYOU_GRACE_HOURS = 2


def foryou_threshold(now) -> (Q):
    """Entry threshold for the For You feed (see constants above)."""
    grace_start = now - timedelta(hours=FORYOU_GRACE_HOURS)
    return (
        Q(unique_commenters_count__gte=FORYOU_MIN_COMMENTERS)
        | Q(unique_reactors_count__gte=FORYOU_MIN_REACTORS)
        | Q(create_at__gte=grace_start)
    )


# ── For You personalization (PHASE 2) ───────────────────────────────────
# The client sends its top tags in the POST BODY — the affinity profile,
# which lives ONLY in its localStorage. For the server they are EPHEMERAL:
# they live only as long as this request, like a multi-tag search — they
# are NOT persisted, NOT associated with any mask, there is NO profile
# table.
#
# The algorithm separates two things:
#   STEP 1 (composition): the CANDIDATES are the UNION of the global top by
#     momentum (discovery) + the posts with the user's tags (even if their
#     base momentum is low — if they don't enter, the boost couldn't lift
#     them). Each group bounded to FORYOU_MIX_POOL.
#   STEP 4 (ordering): ALL candidates are sorted together by momentum_final
#     DESC, computed AT SERVE TIME:
#       momentum_final = base × region_boost × affinity_boost
# Tag cap per request: with POST there is no longer a URL limit, but
# filtering/counting against many tags is heavy in the query (Raspberry) —
# the client sends its top ~10 and this truncates defensively.
FORYOU_TAGS_MAX = 15
# Max candidate pool PER GROUP: bounds memory/CPU per request on limited
# hardware (nobody paginates beyond ~500 posts in one session).
FORYOU_MIX_POOL = 500

# ── Affinity boost (weight of YOUR tags in the ranking) ──────────────────
# affinity_boost = 1 + K × matching_tags (capped at FORYOU_AFFINITY_MAX_TAGS)
#   0 matching → ×1.00 | 1 → ×1.35 | 2 → ×1.70 | 3+ → ×2.05
# Raising K = more personalization; lowering it = more subtle. Without tags
# in the query (cold start) → ×1.0: the global top stays intact.
FORYOU_AFFINITY_K = 0.35
FORYOU_AFFINITY_MAX_TAGS = 3

# ── Regional boost (STEP 4) ──────────────────────────────────────────────
# momentum_final = momentum_base × 1.5 if post.region == reader's region.
# DYNAMIC, at query time: the precomputed momentum_score (base) is NEVER
# modified — each reader sees THEIR ranking. The region is DECLARED by the
# FE (?region=, derived from navigator.language) and the BA only sanitizes
# it — NO GeoIP/IP: it is a feed preference, not a security boundary.
FORYOU_REGION_BOOST = 1.5

# Fields of the For You id-first sub-queries.
FORYOU_ROW_FIELDS = ("id", "momentum_score", "create_at", "region")

# ── Close You (near me, ~10 km) ──────────────────────────────────────────
# SAME ENGINE as For You (threshold, grace, precomputed momentum, POST
# pagination, serializer — see __feed_querysets/__serve_feed_page); only
# the FILTER (geohash cells instead of tags) and the BOOST (proximity
# instead of affinity+region) change:
#   momentum_final = momentum_base × proximity_boost
# The reader sends its geohash cell (precision 5, fuzzed) + 8 neighbors in
# the POST BODY — EPHEMERAL: they are never persisted (there is no map of
# people's locations). Only POSTS store a cell (author opt-in).
# DECREASING proximity boost per ring:
#   boost = 1 + K × (1 − ring/n_rings)
#   → reader's cell (ring 0): ×1.35 | edge of radius: ×1.0
# "The closest rises" holds even when the radius is 100 km.
CLOSEYOU_PROXIMITY_K = 0.35
# Feed radius: the reader expands it at will (clamp 1-100, default 15).
CLOSEYOU_RADIUS_DEFAULT_KM = 15.0
CLOSEYOU_RADIUS_MIN_KM = 1.0
CLOSEYOU_RADIUS_MAX_KM = 100.0
CLOSEYOU_ROW_FIELDS = ("id", "momentum_score", "create_at", "geohash")


def parse_closeyou_radius(raw) -> (float):
    """Clamp the declared radius: 1-100 km; invalid/absent → 15."""
    try:
        radius = float(raw)
    except (TypeError, ValueError):
        return CLOSEYOU_RADIUS_DEFAULT_KM
    return max(CLOSEYOU_RADIUS_MIN_KM,
               min(CLOSEYOU_RADIUS_MAX_KM, radius))


def foryou_final_momentum(momentum, post_region, user_region,
                          matched_tags) -> (float):
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
        final *= 1 + FORYOU_AFFINITY_K * min(
            matched_tags, FORYOU_AFFINITY_MAX_TAGS)
    return final


def rank_foryou_rows(rows, user_region, tag_counts=None) -> (list):
    """
    STEP 4 — SORT: momentum_final DESC (tie-break by date) over the
    BOUNDED set of candidates. Each row is (id, momentum_score,
    create_at, region); tag_counts = {thread_id: matching_tags}.
    """
    counts = tag_counts or {}

    def sort_key(row):
        thread_id, momentum, created, region = row
        return (
            foryou_final_momentum(
                momentum, region, user_region, counts.get(thread_id, 0)),
            created,
        )

    return [row[0] for row in sorted(rows, key=sort_key, reverse=True)]


def union_foryou_candidates(tagged_rows, global_rows) -> (list):
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
    permission_classes = (IsClientAuthenticated, )
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ("create_at", "reactions_count")
    ordering = ("-create_at",)

    def get_queryset(self) -> (QuerySet):
        now_date = timezone.localtime(timezone.now())
        queryset = super().get_queryset().filter(
            Q(expire_date__gte=now_date)|
            Q(expire_date__isnull=True),
            is_active=True
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
            if (query and len(query) > MAX_QUERY_LENGTH) or (
                tag and len(tag) > MAX_QUERY_LENGTH
            ):
                raise ValidationError(
                    {"detail": f"Query exceeds {MAX_QUERY_LENGTH} characters."}
                )
            # with_card_relations: preloads mask/media/reactions and
            # annotates responses_count — eliminates the serializer's N+1 in
            # the lists.
            threads = with_card_relations(
                queryset.filter(
                    visibility=True,
                    is_active=True,
                    sub__isnull=True),
                self.request.mask,
            )

            if tag:
                # EXACT tag (Thread-Tag relation) but case- and
                # accent-insensitive: unaccent(name) = unaccent(tag) → #peru
                # finds #perú. distinct(): a post with the same hashtag
                # repeated creates several Tag rows and the join would
                # duplicate the thread.
                return threads.filter(
                    tag__name__unaccent__iexact=strip_accents(tag)
                ).distinct().order_by("-create_at")

            if query:
                # icontains + unaccent on both sides (field and query):
                # case- and accent-insensitive, same as /search/.
                return threads.filter(
                    text__unaccent__icontains=strip_accents(query)
                ).order_by("-create_at")

            return threads.order_by("-create_at")

        return queryset

    def list(self, request) -> (Response):
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
        serializer = self.get_serializer(
            pages,
            many=True,
            context=({"mask": request.mask, "short": True}))

        return self.get_paginated_response(({
            "data": serializer.data}))

    @action(detail=False, methods=["GET"])
    def mine(self, request) -> (Response):
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
        serializer = self.get_serializer(
            pages,
            many=True,
            context=({"mask": request.mask, "short": True}))

        return self.get_paginated_response(({
            "data": serializer.data}))

    def retrieve(self, request, pk) -> (Response):
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
        thread = get_object_or_404(self.get_queryset())
        serializer = self.get_serializer(
            thread, many=False, context=({
                "mask": request.mask})).data

        return Response(serializer, status=HTTP_200_OK)

    def create(self, request) -> (Response):
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
        text = request.data.get("text")
        if sub_tread:
            sub_tread = get_object_or_404(
                Thread.objects.filter(uid=sub_tread)).uid

        serializer = self.get_serializer(data=request.data, context=({
            "mask": request.mask,
            "show_responses": True
        }))
        serializer.is_valid(raise_exception=True)
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
        )
        return Response(serializer.data, status=HTTP_201_CREATED)

    @action(detail=False, methods=["POST"])
    def foryou(self, request) -> (Response):
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
    def closeyou(self, request) -> (Response):
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
        radius ×1.0 (NO affinity or region). Fallback: newest LOCAL of the
        area (never posts from somewhere else in the world).

        Response codes:

            200 - Obtains the Close You page of threads.
            400 - Missing/invalid reader geohash.
            401 - The client is not authorized.
        """
        body = request.data if isinstance(request.data, dict) else {}
        # Compat: clients that still send cells[] → the first is the center.
        raw_center = body.get("geohash") or (
            (body.get("cells") or [None])[0]
            if isinstance(body.get("cells"), (list, tuple)) else None
        )
        center = normalize_geohash(raw_center)
        if not center:
            return Response(
                {"detail": "geohash (reader cell) required."},
                status=HTTP_400_BAD_REQUEST)

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

        rows = list(
            qualifying.filter(area_filter)
            .order_by(*order)
            .values_list(*CLOSEYOU_ROW_FIELDS)[:FORYOU_MIX_POOL]
        )

        # Decreasing proximity boost: post → cell → normalized distance
        # (0=center, 1=edge) → boost, all in memory O(candidates), no N+1.
        def proximity_final(momentum, geohash) -> (float):
            key = geohash if precision == GEOHASH_PRECISION \
                else (geohash or "")[:GEOHASH_PRECISION - 1]
            ring = ring_by_cell.get(key, 1.0)
            return momentum * (1 + CLOSEYOU_PROXIMITY_K * (1 - ring))

        ordered_ids = [
            row[0] for row in sorted(
                rows,
                key=lambda r: (proximity_final(r[1], r[3]), r[2]),
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
            momentum_final_fn=lambda t: proximity_final(
                t.momentum_score, t.geohash),
        )

    def __parse_foryou_tags(self, body) -> (list):
        """
        Normalizes the tags from the BODY — accepts a JSON list (["a","b"])
        or a comma-separated string ("a,b") — same as how Tags are stored
        (lowercase, no '#', no accents — name_norm). Defensive truncation to
        FORYOU_TAGS_MAX (query cost). The tags are EPHEMERAL: they live in
        this request and are discarded — they are never persisted nor
        associated with the request's mask.
        """
        raw = body.get("tags", "")
        pieces = raw if isinstance(raw, (list, tuple)) \
            else str(raw or "").split(",")
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

    def __feed_querysets(self) -> (tuple):
        """(visible, qualifying): the feed universe + entry threshold with
        grace window — identical for For You and Close You."""
        now_date = timezone.localtime(timezone.now())
        visible = Thread.objects.filter(
            Q(expire_date__gte=now_date) | Q(expire_date__isnull=True),
            is_active=True,
            visibility=True,
            sub__isnull=True,
        )
        return visible, visible.filter(foryou_threshold(timezone.now()))

    def __serve_feed_page(self, request, ordered_ids, filler_qs,
                          momentum_final_fn) -> (Response):
        """
        Shared serving engine: newest FALLBACK (over filler_qs — global in
        For You, local in Close You), pagination of the id list, rich
        queryset (with_card_relations, no N+1) ONLY for the page,
        momentum_final per query and the card serializer.
        """
        page_size = self.paginator.get_page_size(request)
        if len(ordered_ids) < page_size and filler_qs is not None:
            seen = set(ordered_ids)
            filler = filler_qs.order_by("-create_at").values_list(
                "id", flat=True)[:FORYOU_MIX_POOL]
            ordered_ids += [i for i in filler if i not in seen]

        page_ids = self.paginate_queryset(ordered_ids)
        threads = with_card_relations(
            Thread.objects.filter(id__in=page_ids), request.mask)
        by_id = {t.id: t for t in threads}
        page = [by_id[i] for i in page_ids if i in by_id]

        # momentum_final = the SAME formula that ordered this feed,
        # computed per query — the DB's base momentum never changes.
        for thread in page:
            thread.momentum_final = momentum_final_fn(thread)

        serializer = self.get_serializer(
            page,
            many=True,
            context=({"mask": request.mask, "short": True}))

        return self.get_paginated_response(({
            "data": serializer.data}))

    def __foryou_response(self, request, threshold, tags, region, lang) -> (Response):
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

        def build_candidates(qualifying) -> (tuple):
            # Global candidates (discovery / cold start): top-N by index.
            # The tuples (id, momentum, create_at, region) allow boost +
            # re-rank of the union in Python without more queries. Note:
            # ENTRY to the pool is by base momentum; the boost reorders
            # within the pool (deliberate approximation — exact for every
            # post in the top-500, cheap for the Raspberry).
            global_rows = list(
                qualifying.order_by(*order)
                .values_list(*FORYOU_ROW_FIELDS)[:FORYOU_MIX_POOL]
            )

            if not tags:
                return rank_foryou_rows(global_rows, region), {}

            # Posts with the user's tags — they enter the pool even if their
            # base momentum is low (the affinity boost lifts them). The join
            # with Tag duplicates rows if a post matches several tags: it is
            # deduplicated in Python (dict.fromkeys preserves order) instead
            # of DISTINCT, which in Postgres clashes with the ORDER BY of
            # non-selected columns.
            tagged_raw = qualifying.filter(
                tag__name_norm__in=tags
            ).order_by(*order).values_list(
                *FORYOU_ROW_FIELDS)[:FORYOU_MIX_POOL * 2]
            tagged_rows = list(
                dict.fromkeys(tagged_raw))[:FORYOU_MIX_POOL]
            candidate_rows = union_foryou_candidates(
                tagged_rows, global_rows)

            # matching_tags per candidate: ONE bounded query (only the pool
            # ids), COUNT(DISTINCT name_norm) — no N+1. Ephemeral: lives only
            # as long as the request.
            tag_counts = dict(
                Tag.objects.filter(
                    thread_id__in=[row[0] for row in candidate_rows],
                    name_norm__in=tags,
                ).values("thread_id").annotate(
                    c=Count("name_norm", distinct=True)
                ).values_list("thread_id", "c")
            )
            return rank_foryou_rows(
                candidate_rows, region, tag_counts), tag_counts

        # STEP 2: hard filter by language (indexed language) if the FE
        # declared it; without ?lang= it does not filter (old clients).
        qualifying = (
            base_qualifying.filter(language=lang)
            if lang else base_qualifying
        )
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
                t.momentum_score, t.region, region,
                tag_counts.get(t.id, 0)),
        )

    @action(detail=True, methods=["GET"])
    def responses(self, request, pk) -> (Response):
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
        """
        thread = get_object_or_404(self.get_queryset())
        head_serializer = self.get_serializer(
            thread, many=False, context=({
                "mask": request.mask}))

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
        serializer = self.get_serializer(pages, many=True, context=({
            "mask": request.mask, "show_responses": True}))

        return self.get_paginated_response(({
            "data": serializer.data,
            "context": {"head": head_serializer.data}
        }))
