# Python
import hashlib
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

# Django
from django.core.cache import cache
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.db.models.query import QuerySet

# Django
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.status import HTTP_400_BAD_REQUEST
from rest_framework.viewsets import GenericViewSet

from app.constants.search import (
    ACTIVITY_DAYS,
    MAX_QUERY_LENGTH,
    MEDIA,
    POSTS,
    SUGGEST_MIN_CHARS,
    SUGGEST_SNIPPET_RADIUS,
    SUGGEST_TAGS_LIMIT,
    SUGGEST_THREADS_LIMIT,
    SUGGEST_TRENDING_LIMIT,
    TAGS,
    USERS,
    VALID_TYPES,
)
from app.methods.moderation import find_blocked_terms, shadowban_matching
from app.methods.threads import attach_top_replies, with_card_relations
from app.models.mask import Mask
from app.models.media import ThreadFile
from app.models.tag import Tag

# Models
from app.models.thread import Thread
from app.models.trending_tag import TrendingTag

# Libs
from app.permissions.client import IsClientAuthenticated
from app.permissions.throttling import TrustedIPScopedRateThrottle
from app.rest.pagination import SearchPagination
from app.rest.serializers.media_serializer import ThreadMediaSerializer
from app.rest.serializers.search_serializers import (
    TagSearchSerializer,
    UserSearchSerializer,
)

# Serializers
from app.rest.serializers.thread_serializer import ThreadSerializer
from app.utils.text import strip_accents


class SearchViewSet(GenericViewSet):
    """
    Global search by `q` query param over posts, tags, users and media.

    Single-endpoint design (option A): it ALWAYS returns the `counts` dict
    with the 4 type keys + the paginated results of the active type
    (`type`). Only the ACTIVE type is actually counted — the other three
    keys report 0 (the frontend deliberately renders no tab badges, so the
    inactive COUNT queries were per-request cost with no consumer).

        GET /search/?q=<query>&type=posts|tags|users|media[&page=N]

        {
            "counts": {"posts": 10, "tags": 0, "users": 0, "media": 0},
            "count": 10,            # total of the active type (pagination)
            "next": "http://.../search/?q=a&type=posts&page=2",
            "previous": null,
            "results": [ ... ]      # results of the active type
        }

    `type=media` is the IG-explore style gallery tab: each result is ONE
    tile per matching thread — its first media file (same shape as a thread
    card's `media` items: uid, file, is_video, is_nsfw, width, height,
    target_color) plus `media_count` (the thread's total active files, for
    the client's carousel mark) and `thread`, the parent thread's full
    card, so the client can seed the preview panel and navigate without a
    second request.

    All search is case-insensitive and accent-insensitive: posts/tags match
    against the *_norm columns (text_norm / name_norm — lowercase, accent
    stripped at WRITE time with strip_accents) and the query is normalized
    in Python with the SAME function — with BOTH sides normalized by the
    same code, 'JUDÍOS', 'judios', 'Judíos' and 'judíos' return exactly the
    same. Matching the *_norm columns (plain LIKE '%…%', no UPPER/UNACCENT
    wrappers) lets the GIN pg_trgm indexes serve the search — the previous
    __unaccent__icontains lookups Seq Scanned the whole table per request
    (measured with EXPLAIN: the trigram index was never used, idx_scan=0).
    The posts reuse the feed's ThreadSerializer → same shape → same mapper
    in the frontend.
    """

    queryset = Thread.objects.none()
    pagination_class = SearchPagination
    serializer_class = ThreadSerializer
    permission_classes = (IsClientAuthenticated,)
    throttle_classes = (TrustedIPScopedRateThrottle,)

    def get_throttles(self):
        # Rate limit anónimo por IP (scopes en DEFAULT_THROTTLE_RATES):
        # 'search_suggest' es más alto (se dispara al tipear, con debounce);
        # el resto cae en 'search'. Defensa server-side contra martilleo.
        self.throttle_scope = "search_suggest" if self.action == "suggest" else "search"
        return super().get_throttles()

    def _validate_query_length(self, raw):
        """
        Defensa server-side BARATA y PRIMERO: rechaza queries sobre el tope
        ANTES de normalizar o tocar la BD. Devuelve una Response 400 limpia
        (sin eco del query — privacidad/no filtra payload) o None si pasa.
        """
        if raw and len(raw) > MAX_QUERY_LENGTH:
            return Response(
                {"detail": f"Query exceeds {MAX_QUERY_LENGTH} characters."},
                status=HTTP_400_BAD_REQUEST,
            )
        return None

    def _empty_search_response(self) -> Response:
        """The 'nothing exists' shape: same contract ({counts, count, next,
        previous, results}) with everything at zero. Used by the empty query
        AND by blocklisted queries — a shadowbanned term must be
        indistinguishable from a term nobody ever posted about."""
        self.paginate_queryset(Thread.objects.none())
        return self.get_paginated_response(
            {
                "data": [],
                "context": {
                    "counts": {POSTS: 0, TAGS: 0, USERS: 0, MEDIA: 0},
                },
            }
        )

    # -- Querysets per type --------------------------------------------------

    # Órdenes permitidos de la búsqueda de posts:
    #   top    → MOMENTUM precomputado (misma señal de relevancia que For
    #            You), desempate por fecha. Reusa el campo del cron — NO
    #            recalcula la fórmula ni aplica boosts/umbral de For You
    #            (búsqueda = acción explícita, neutral y barata). El índice
    #            compuesto (-momentum_score, -create_at) cubre el ORDER BY.
    #   latest → cronológico puro.
    POST_ORDERINGS = {
        "-momentum_score": ("-momentum_score", "-create_at"),
        "-create_at": ("-create_at",),
    }
    DEFAULT_POST_ORDERING = "-momentum_score"

    # El identificador PÚBLICO de un autor es @hash[0:6] (lo que se ve en
    # cada hilo). El hash completo, el id (UUID PK) y el uid NUNCA se
    # buscan — solo este prefijo de 6: en una app anónima, exponer un id
    # interno permitiría correlacionar identidades. DEBE coincidir con el
    # [0:6] de Mask.__str__ y del slice del frontend.
    MASK_PUBLIC_LEN = 6
    _MASK_PUBLIC_RE = re.compile(r"^[0-9a-f]{%d}$" % MASK_PUBLIC_LEN)

    def _author_mask_query(self, raw):
        """
        Devuelve el prefijo de hash a buscar si `raw` es EXACTAMENTE un
        identificador público de máscara (6 hex, con o sin '@' inicial);
        si no, None. Solo coincidencia exacta y completa del id PÚBLICO —
        ni parciales ni el hash interno.
        """
        candidate = (raw or "").strip().lstrip("@").lower()
        return candidate if self._MASK_PUBLIC_RE.match(candidate) else None

    def _posts_base_queryset(self, query, author_mask=None) -> QuerySet:
        """Filtro PLANO de la pestaña posts (mismo universo que el feed:
        activos, visibles, raíz) + match de texto sobre text_norm — sin
        annotates ni joins: es lo que cuenta el COUNT de la pestaña (y el
        paginador lo reusa). El match con text_norm__contains (query ya
        normalizado con strip_accents+lower, MISMA función que escribe la
        columna) usa el índice GIN trigram; el anterior
        text__unaccent__icontains hacía Seq Scan de toda la tabla (medido).

        Si `author_mask` (id público exacto) viene, se INCLUYEN además los
        hilos de ese autor — el OR se resuelve con mask_id__in sobre los ids
        (0-1 masks, resueltos con una query barata por el índice de prefijo
        de hash) en vez de mask__hash sobre el JOIN: un OR que cruza un join
        bloqueaba el bitmap-OR del planner (medido: hash join + Seq Scan,
        37 ms). Sin duplicar filas (un hilo tiene una sola mask)."""
        now_date = timezone.localtime(timezone.now())
        match = Q(text_norm__contains=query)
        if author_mask:
            author_ids = list(Mask.objects.filter(hash__startswith=author_mask).values_list("id", flat=True))
            if author_ids:
                match |= Q(mask_id__in=author_ids)
        return Thread.objects.filter(
            Q(expire_date__gte=now_date) | Q(expire_date__isnull=True),
            match,
            is_active=True,
            visibility=True,
            is_private=False,
            sub__isnull=True,
        )

    def _posts_queryset(self, base, ordering=None) -> QuerySet:
        """Queryset RICO de la página (card relations) sobre el filtro
        plano. Sin annotate de reactions_count: el serializer SIEMPRE lo
        recalcula desde prefetched_reactions (with_card_relations), así que
        ese Count(distinct) solo añadía un JOIN + GROUP BY sobre TODOS los
        matches antes del LIMIT (medido: 30 ms vs 2 ms la misma página).
        `ordering` lo decide el FE (top vs latest); whitelist arriba."""
        order = self.POST_ORDERINGS.get(ordering, self.POST_ORDERINGS[self.DEFAULT_POST_ORDERING])
        return with_card_relations(
            base.order_by(*order),
            getattr(self.request, "mask", None),
        )

    def _media_queryset(self, posts_base) -> QuerySet:
        """Media tab (IG-explore style gallery): ONE tile per matching
        thread — its FIRST active file in upload order. (The previous
        Twitter-style one-row-per-file contract made a multi-image thread
        occupy N consecutive tiles, which read as duplicates in the grid;
        the client shows a carousel mark instead, via `media_count`.)
        Built on top of the FLAT posts filter (same universe — active,
        visible, root, not expired, same text/author match) as a semi-join:
        the thread side is served by the same trigram index as the posts
        count and ThreadFile.thread_id is the FK index. The first-per-thread
        cut is a correlated LIMIT 1 subquery on the same FK index. Ordered
        by the parent thread's relevance (precomputed momentum, date as
        tiebreaker)."""
        first_per_thread = (
            ThreadFile.objects.filter(
                is_active=True,
                thread=OuterRef("thread"),
            )
            .order_by("create_at", "pk")
            .values("pk")[:1]
        )
        return ThreadFile.objects.filter(
            is_active=True,
            thread__in=posts_base.order_by().values("pk"),
            pk__in=Subquery(first_per_thread),
        ).order_by("-thread__momentum_score", "-thread__create_at")

    def _media_results(self, files) -> list:
        """Serializes a page of media items and attaches each one's parent
        thread card so the gallery can seed the thread panel / "View thread"
        navigation without a second request, plus `media_count` (the parent
        thread's TOTAL active files) so the grid can mark multi-media
        threads with a carousel indicator. Efficiency: the card is
        serialized ONCE per thread, the counts come from ONE grouped COUNT
        over the page's thread ids, and the threads are hydrated with the
        same prefetch set the feed uses (with_card_relations) — a constant
        number of queries for the whole page, no per-item lookups."""
        files = list(files or [])
        results = ThreadMediaSerializer(files, many=True).data
        mask = getattr(self.request, "mask", None)
        thread_ids = {f.thread_id for f in files}
        cards = {}
        file_counts = {}
        if thread_ids:
            threads = with_card_relations(Thread.objects.filter(pk__in=thread_ids), mask)
            cards = {
                t.pk: ThreadSerializer(
                    t,
                    many=False,
                    context={"mask": mask, "short": True},
                ).data
                for t in threads
            }
            file_counts = dict(
                ThreadFile.objects.filter(
                    is_active=True,
                    thread_id__in=thread_ids,
                )
                .values("thread_id")
                .annotate(n=Count("id"))
                .values_list("thread_id", "n")
            )
        for item, file in zip(results, files):
            item["thread"] = cards.get(file.thread_id)
            item["media_count"] = file_counts.get(file.thread_id, 1)
        return results

    def _tags_queryset(self, query) -> QuerySet:
        """Groups the Tag rows by name and counts the distinct threads
        (posts per tag). El match va sobre name_norm (lowercase, sin
        acentos, escrito por create_tags con strip_accents — la MISMA
        normalización del query): name_norm__contains usa el índice GIN
        trigram; el anterior name__unaccent__icontains escaneaba toda la
        tabla en cada request (medido)."""
        return (
            Tag.objects.filter(is_active=True, name_norm__contains=query)
            .values("name")
            .annotate(count=Count("thread", distinct=True))
            .order_by("-count", "name")
        )

    def _attach_activity(self, tags_page) -> list:
        """
        Adds to each tag of the page its `activity` time series: posts
        (distinct threads) per day over the last ACTIVITY_DAYS days, in
        chronological order.

        Efficiency: a SINGLE aggregated query for all the tags of the page
        (not one per tag). It brings back (name, day, count) and in Python
        the days without posts are filled with 0 — critical so the sparkline
        line shows the real valleys and all arrays have ACTIVITY_DAYS values.

        The days are grouped in the project's timezone (TIME_ZONE, USE_TZ):
        TruncDate converts to local date and timezone.localdate() gives the
        local "today", so the buckets line up.
        """
        tags_page = list(tags_page or [])
        names = [t["name"] for t in tags_page]
        if not names:
            return tags_page

        today = timezone.localdate()

        # Light cache (LocMem, TTL 5 min): the TruncDate aggregation is one
        # of the most expensive parts of search and the sparklines don't
        # change by the second. On limited hardware we prefer this to adding
        # Redis (footprint).
        names_key = hashlib.md5(",".join(sorted(names)).encode()).hexdigest()
        cache_key = f"tag_activity:{today}:{names_key}"
        cached_activity = cache.get(cache_key)
        if cached_activity is not None:
            for tag in tags_page:
                tag["activity"] = cached_activity.get(tag["name"], [0] * ACTIVITY_DAYS)
            return tags_page
        start_day = today - timedelta(days=ACTIVITY_DAYS - 1)
        # Start of the range: local midnight of the first day, as an aware
        # datetime (USE_TZ=True) to filter create_at correctly.
        start = timezone.make_aware(datetime.combine(start_day, time.min))

        rows = (
            Tag.objects.filter(
                is_active=True,
                name__in=names,
                thread__create_at__gte=start,
            )
            .annotate(day=TruncDate("thread__create_at"))
            .values("name", "day")
            .annotate(n=Count("thread", distinct=True))
        )

        # {name: {day: count}} → array per tag filling missing days with 0
        by_tag = defaultdict(dict)
        for row in rows:
            by_tag[row["name"]][row["day"]] = row["n"]

        day_range = [start_day + timedelta(days=i) for i in range(ACTIVITY_DAYS)]
        activity_by_name = {}
        for tag in tags_page:
            day_counts = by_tag.get(tag["name"], {})
            tag["activity"] = [day_counts.get(day, 0) for day in day_range]
            activity_by_name[tag["name"]] = tag["activity"]

        cache.set(cache_key, activity_by_name, 300)  # 5 min
        return tags_page

    def _users_queryset(self, query) -> QuerySet:
        """Masks whose hash starts with the query, with their root post count.

        Prefix match on purpose: the only meaningful author query is the
        public @id (the hash's first 6 hex chars — same rule as
        _author_mask_query and MasksViewSet.retrieve). The previous
        hash__unaccent__icontains wrapped a hex column in UNACCENT (a no-op
        that defeated the index) and seq-scanned ALL masks on every search
        request."""
        return (
            Mask.objects.filter(is_active=True, hash__startswith=query)
            .annotate(
                posts_count=Count(
                    "thread",
                    filter=Q(
                        thread__is_active=True,
                        thread__visibility=True,
                        thread__is_private=False,
                        thread__sub__isnull=True,
                    ),
                    distinct=True,
                )
            )
            .order_by("-posts_count", "-create_at")
        )

    @action(detail=False, methods=["GET"])
    def suggest(self, request) -> Response:
        """
        Autocomplete estilo Twitter — BARATO (índices + LIMIT + tendencias
        precomputadas; nada se calcula por request). Privacidad: las
        queries NO se loggean ni se persisten.

          input vacío → {"trending": [{name, count}, ...]}
              top-N tags por score de momentum (TrendingTag, precomputado
              por el cron). Las RECIENTES las pone el FE desde localStorage.

          ≥2 chars → {"tags": [{name, count}, ...],
                      "threads": [{uid, snippet}, ...]}
              tags: prefix sobre TrendingTag.name_norm (indexado),
                    ordenados por tendencia (NO alfabético).
              threads: text__icontains (índice GIN trigram) ordenado por
                    momentum_score; snippet con la coincidencia. Misma
                    normalización que la búsqueda principal (que es MÁS
                    permisiva con unaccent → todo lo sugerido se encuentra).
        """
        raw_param = request.GET.get("q") or ""
        # Longitud PRIMERO (barato), antes de strip/normalizar/BD.
        too_long = self._validate_query_length(raw_param)
        if too_long is not None:
            return too_long

        raw = raw_param.strip().lstrip("#")
        normalized = strip_accents(raw).lower()

        if len(normalized) < SUGGEST_MIN_CHARS:
            trending = list(
                TrendingTag.objects.order_by("-score").values("name", "thread_count")[:SUGGEST_TRENDING_LIMIT]
            )
            items = [{"type": "tag", "name": t["name"], "count": t["thread_count"]} for t in trending]
            # REAL activity series for the landing sparklines — the same
            # aggregation (and 5-min LocMem cache) the tags tab uses. The
            # trending names are identical for every user, so this resolves
            # to ONE cached aggregate shared app-wide, not a per-request
            # cost.
            self._attach_activity(items)
            return Response({"trending": items})

        # Shadowban blocklist: a blocked input suggests NOTHING — the same
        # shape a term nobody posted about returns. Read-only here (no
        # sweep): suggest fires on every keystroke; the deactivation runs
        # once, on the real search.
        if find_blocked_terms(normalized):
            return Response({"tags": [], "threads": []})

        # TAGS: prefix sobre la tabla de tendencias (indexada), por score.
        tag_rows = (
            TrendingTag.objects.filter(name_norm__startswith=normalized)
            .order_by("-score")
            .values("name", "thread_count")[:SUGGEST_TAGS_LIMIT]
        )
        tags = [{"type": "tag", "name": r["name"], "count": r["thread_count"]} for r in tag_rows]

        # HILOS: contenido por momentum. MISMA normalización y MISMA columna
        # que la búsqueda principal (text_norm, strip_accents+lower en ambos
        # lados) → insensible a acentos: 'bogota' encuentra 'Bogotá', y lo
        # que sugiere = lo que encuentra la búsqueda al tocarlo. Además el
        # LIKE plano sobre text_norm usa el índice GIN trigram (el lookup
        # __unaccent__icontains escaneaba toda la tabla con términos raros).
        thread_rows = (
            Thread.objects.filter(
                is_active=True,
                visibility=True,
                is_private=False,
                sub__isnull=True,
                text_norm__contains=normalized,
            )
            .order_by("-momentum_score", "-create_at")
            .values("uid", "text")[:SUGGEST_THREADS_LIMIT]
        )
        threads = [
            {"type": "thread", "uid": r["uid"], "snippet": self.__snippet(r["text"], normalized)} for r in thread_rows
        ]

        return Response({"tags": tags, "threads": threads})

    def __snippet(self, text, term) -> str:
        """Fragmento alrededor de la primera coincidencia, para mostrar el
        contexto en el dropdown. La búsqueda del match es INSENSIBLE a
        acentos/caso (strip_accents en ambos lados) pero devuelve el slice
        del texto ORIGINAL (con sus tildes). Cheap: pocos hilos."""
        text = text or ""
        # `term` ya viene normalizado (strip_accents+lower); normalizamos el
        # texto igual SOLO para localizar el índice — los offsets coinciden
        # porque strip_accents no cambia la longitud (sustituye 1:1).
        idx = strip_accents(text).lower().find(term)
        if idx < 0:
            return text[: SUGGEST_SNIPPET_RADIUS * 2].strip()
        start = max(0, idx - SUGGEST_SNIPPET_RADIUS)
        end = min(len(text), idx + len(term) + SUGGEST_SNIPPET_RADIUS)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        return snippet

    def list(self, request) -> Response:
        # Normalize the query in Python with strip_accents + lower — the
        # SAME normalization that writes text_norm/name_norm, so both sides
        # of the LIKE match exactly ('JUDÍOS', 'judios', 'Judíos' → same)
        # and the plain LIKE can use the trigram indexes.
        raw_param = request.GET.get("q") or ""
        # Longitud PRIMERO (barato), antes de strip/normalizar/BD.
        too_long = self._validate_query_length(raw_param)
        if too_long is not None:
            return too_long

        # El '@' inicial es un sigilo de máscara, no texto: se quita para
        # el match de texto (así '@2b283c' y '2b283c' dan el mismo result).
        query = strip_accents(raw_param.strip().lstrip("@")).lower()
        # ¿La query es EXACTAMENTE un id público de máscara (@2b283c)? Si
        # sí, top/latest incluyen además los hilos de ese autor.
        author_mask = self._author_mask_query(raw_param)
        search_type = request.GET.get("type", POSTS)
        if search_type not in VALID_TYPES:
            search_type = POSTS

        # Empty query => we don't return the whole universe (icontains=""
        # matches everything). We keep the {counts, results} contract with
        # empty values.
        if not query:
            return self._empty_search_response()

        # Shadowban blocklist: a query carrying a blocked term (whole-word,
        # both sides normalized) answers exactly like a no-match search —
        # as if that content never existed — and every active thread/tag
        # still carrying the term is soft-deleted on the spot (the purge GC
        # hard-deletes it later). Privacy rule holds: the query is neither
        # logged nor echoed.
        blocked = find_blocked_terms(query)
        if blocked:
            shadowban_matching(blocked)
            return self._empty_search_response()

        posts_base = self._posts_base_queryset(query, author_mask=author_mask)
        posts_qs = self._posts_queryset(posts_base, request.GET.get("ordering"))
        media_qs = self._media_queryset(posts_base)
        # Tags are stored WITHOUT '#': strip a leading '#' so '#dns' and 'dns'
        # match the same tag (and yield the same tags count). posts/users keep
        # the raw query, so the hashtag text search is preserved. Same
        # normalization as the suggest endpoint.
        tags_qs = self._tags_queryset(query.lstrip("#"))
        users_qs = self._users_queryset(query)

        # Count ONLY the active tab. The response keeps the four `counts`
        # keys (frozen contract) but the frontend never renders tab badges,
        # so the three inactive COUNT queries per request were pure cost —
        # they now report 0. In tags_qs the count() of a .values().annotate()
        # queryset returns the number of groups; for posts we count the FLAT
        # filter (no card annotations, no joins): a plain COUNT the trigram
        # index can serve.
        count_for = {
            POSTS: lambda: posts_base.order_by().count(),
            TAGS: lambda: tags_qs.count(),
            USERS: lambda: users_qs.count(),
            # Gallery tiles (one per thread with media) — what the tab
            # paginates.
            MEDIA: lambda: media_qs.order_by().count(),
        }
        counts = {POSTS: 0, TAGS: 0, USERS: 0, MEDIA: 0}
        counts[search_type] = count_for[search_type]()
        # The paginator (SearchPagination) reuses the active tab's count —
        # without this it would re-issue the SAME expensive COUNT over the
        # annotated queryset (measured: 2 redundant COUNTs per request).
        self.precomputed_count = counts[search_type]

        if search_type == TAGS:
            # Only the tags of the current page carry an activity series.
            page = self._attach_activity(self.paginate_queryset(tags_qs))
            results = TagSearchSerializer(page, many=True).data
        elif search_type == MEDIA:
            page = self.paginate_queryset(media_qs)
            results = self._media_results(page)
        elif search_type == USERS:
            page = self.paginate_queryset(users_qs)
            results = UserSearchSerializer(page, many=True).data
        else:
            page = self.paginate_queryset(posts_qs)
            results = ThreadSerializer(
                page,
                many=True,
                context={"mask": request.mask, "short": True},
            ).data
            # Conversation preview (X-style): the search posts tab serves the
            # same two-story cards as the feeds (shared gate/helper).
            attach_top_replies(results, page, request.mask)

        return self.get_paginated_response(
            {
                "data": results,
                "context": {"counts": counts},
            }
        )
