# Python
import time
import logging
from datetime import timedelta
from collections import defaultdict
from itertools import chain

# Django
from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q
from django.utils import timezone

# Models
from app.models.thread import Thread
from app.models.reaction_relation import ReactionRelation
from app.models.momentum_log import MomentumLog
from app.models.tag import Tag
from app.models.trending_tag import TrendingTag

LOGGER = logging.getLogger(__name__)


# ── Momentum formula parameters ─────────────────────────────────────────
#   points = unique_reactors
#          + unique_commenters                 × COMMENTER_WEIGHT
#          + commenters_replied_by_author      × AUTHOR_REPLY_WEIGHT
#          [+ log10(views + 1) × 2 → OMITTED in v1: there is no views counter]
#   momentum_score = points / (age_hours + AGE_SOFTENER_HOURS)^DECAY_EXPONENT
#
# Golden counting rule: each signal measures DISTINCT MASKS and EXCLUDES the
# thread author (one person = one vote, nobody votes for themselves).
COMMENTER_WEIGHT = 3
AUTHOR_REPLY_WEIGHT = 5
DECAY_EXPONENT = 1.5
AGE_SOFTENER_HOURS = 2  # softens the first few hours

# Top-N de tags en tendencia que precomputa el cron (tabla pequeña).
TRENDING_TAGS_LIMIT = 100

# Fields the job writes (the ones the For You endpoint reads).
UPDATED_FIELDS = (
    "momentum_score",
    "unique_reactors_count",
    "unique_commenters_count",
)


class Command(BaseCommand):
    help = (
        "Recalcula momentum_score y los contadores del For You para los "
        "posts raíz de la ventana activa (default 30 días). Idempotente y "
        "barato: 3 queries agregadas + bulk_update en lotes. Un scheduler "
        "externo lo ejecuta cada 10 min (EventBridge Scheduler en prod, el "
        "servicio `momentum` de docker-compose en local); tambien invocable a "
        "mano para debug o backfill: make recompute_momentum"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help=(
                "Ventana activa en días. Posts más viejos no se recalculan: "
                "a 30 días el divisor (722h)^1.5 deja cualquier puntaje en "
                "~0, no vale el cómputo."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Tamaño de lote del bulk_update.",
        )

    def handle(self, *args, **options):
        started = time.monotonic()
        LOGGER.info(
            "recompute_momentum: iniciando reconteo (ventana=%sd, " "batch=%s)",
            options["days"],
            options["batch_size"],
        )

        # Each run leaves its record in MomentumLog (read-only log in the
        # admin): success with its metrics, or the error if the recount
        # blew up — and in that case it is re-raised so the worker marks
        # the task as failed.
        try:
            processed, updated = self._recompute(options)
            self._recompute_trending()
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            # .exception includes the full traceback in the log.
            LOGGER.exception(
                "recompute_momentum: FALLÓ tras %sms (ventana=%sd)",
                duration_ms,
                options["days"],
            )
            MomentumLog.objects.create(
                window_days=options["days"],
                duration_ms=duration_ms,
                was_successful=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        elapsed = time.monotonic() - started
        MomentumLog.objects.create(
            window_days=options["days"],
            processed_count=processed,
            updated_count=updated,
            duration_ms=int(elapsed * 1000),
            was_successful=True,
        )

        LOGGER.info(
            "recompute_momentum: OK — %s posts en ventana (%sd), %s "
            "actualizados, %.2fs. Términos: reactores ✓, comentaristas ✓, "
            "diálogo-autor ✓, views ✗ (sin contador en v1).",
            processed,
            options["days"],
            updated,
            elapsed,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"recompute_momentum: {processed} posts en ventana " f"({options['days']}d), {updated} actualizados, " f"{elapsed:.2f}s."
            )
        )

    def _recompute(self, options) -> tuple:
        """Performs the recount and returns (processed, updated)."""
        now = timezone.now()
        window_start = now - timedelta(days=options["days"])
        batch_size = options["batch_size"]

        # Each signal excludes the author; if the thread has no mask (null
        # anonymous author), nobody is "the author" and all masks are counted.
        # The explicit isnull prevents NOT(mask = NULL) from discarding rows.

        # 1/3 — Unique reactors per post: COUNT(DISTINCT mask) ≠ author.
        #       (Facebook model: per-user uniqueness is already guaranteed
        #       by the reactions view; DISTINCT guards against duplicates.)
        unique_reactors = dict(
            ReactionRelation.objects.filter(
                is_active=True,
                thread__is_active=True,
                thread__sub__isnull=True,
                thread__create_at__gte=window_start,
            )
            .filter(Q(thread__mask__isnull=True) | ~Q(mask=F("thread__mask")))
            .values("thread_id")
            .annotate(c=Count("mask", distinct=True))
            .values_list("thread_id", "c")
        )

        # 2/3 — Unique commenters per post: DISTINCT masks ≠ author at
        #       depth 1 (comments) and 2 (replies to comments) —
        #       what the UI generates. 100 comments from 1 person = 1; a
        #       thread where only the author talks = 0. (root, mask) pairs
        #       from both levels are merged into sets so that the same
        #       person commenting at both levels counts ONCE.
        depth1_pairs = (
            Thread.objects.filter(
                is_active=True,
                mask__isnull=False,
                sub__isnull=False,
                sub__sub__isnull=True,
                sub__is_active=True,
                sub__create_at__gte=window_start,
            )
            .filter(Q(sub__mask__isnull=True) | ~Q(mask=F("sub__mask")))
            .values_list("sub_id", "mask_id")
            .distinct()
        )

        depth2_pairs = (
            Thread.objects.filter(
                is_active=True,
                mask__isnull=False,
                sub__isnull=False,
                sub__is_active=True,
                sub__sub__isnull=False,
                sub__sub__sub__isnull=True,
                sub__sub__is_active=True,
                sub__sub__create_at__gte=window_start,
            )
            .filter(Q(sub__sub__mask__isnull=True) | ~Q(mask=F("sub__sub__mask")))
            .values_list("sub__sub_id", "mask_id")
            .distinct()
        )

        commenters = defaultdict(set)
        for root_id, mask_id in chain(depth1_pairs.iterator(), depth2_pairs.iterator()):
            commenters[root_id].add(mask_id)

        # 3/3 — Author dialogue: DISTINCT people (≠ author) the author
        #       replied to (author reply, depth 2, to a direct comment from
        #       another person). Self-replies = 0.
        author_replied = dict(
            Thread.objects.filter(
                is_active=True,
                mask__isnull=False,
                mask=F("sub__sub__mask"),  # whoever replies IS the author
                sub__isnull=False,
                sub__is_active=True,
                sub__mask__isnull=False,
                sub__sub__isnull=False,
                sub__sub__sub__isnull=True,  # the grandparent is the root post
                sub__sub__is_active=True,
                sub__sub__create_at__gte=window_start,
            )
            .exclude(sub__mask=F("sub__sub__mask"))  # don't count the author themselves
            .values("sub__sub_id")
            .annotate(c=Count("sub__mask", distinct=True))
            .values_list("sub__sub_id", "c")
        )

        # Score per post, in Python (the ^1.5 power is not done in SQL nor
        # per request: only here, once per run).
        roots = Thread.objects.filter(
            is_active=True,
            sub__isnull=True,
            create_at__gte=window_start,
        ).only("id", "create_at", *UPDATED_FIELDS)

        pending, processed, updated = [], 0, 0
        for thread in roots.iterator(chunk_size=batch_size):
            processed += 1
            reactors = unique_reactors.get(thread.id, 0)
            commenter_count = len(commenters.get(thread.id, ()))
            replied = author_replied.get(thread.id, 0)
            # views: no counter in the model → term omitted (v1).

            points = reactors + commenter_count * COMMENTER_WEIGHT + replied * AUTHOR_REPLY_WEIGHT
            age_hours = (now - thread.create_at).total_seconds() / 3600
            momentum = points / ((age_hours + AGE_SOFTENER_HOURS) ** DECAY_EXPONENT)

            # Skip no-changes: the dead tail (points=0, score=0) is not
            # rewritten — fewer writes on each cron run.
            if thread.momentum_score == momentum and thread.unique_reactors_count == reactors and thread.unique_commenters_count == commenter_count:
                continue

            thread.momentum_score = momentum
            thread.unique_reactors_count = reactors
            thread.unique_commenters_count = commenter_count
            pending.append(thread)

            if len(pending) >= batch_size:
                Thread.objects.bulk_update(pending, UPDATED_FIELDS)
                updated += len(pending)
                pending = []

        if pending:
            Thread.objects.bulk_update(pending, UPDATED_FIELDS)
            updated += len(pending)

        return processed, updated

    def _recompute_trending(self) -> None:
        """
        Tendencias del autocomplete: por NOMBRE de tag, score = suma del
        momentum_score de sus hilos activos. UNA query agregada + un
        bulk_create — reescribe TrendingTag entera (tabla pequeña). El
        endpoint /search/suggest/ solo lee de aquí: cero cálculo por
        request.
        """
        from django.db.models import Sum, Count

        rows = (
            Tag.objects.filter(
                is_active=True,
                thread__is_active=True,
                thread__visibility=True,
                thread__sub__isnull=True,
            )
            .values("name", "name_norm")
            .annotate(
                score=Sum("thread__momentum_score"),
                thread_count=Count("thread", distinct=True),
            )
            .order_by("-score")[:TRENDING_TAGS_LIMIT]
        )
        trending = [
            TrendingTag(
                name=r["name"],
                name_norm=r["name_norm"],
                score=r["score"] or 0,
                thread_count=r["thread_count"],
            )
            for r in rows
        ]
        TrendingTag.objects.all().delete()
        if trending:
            TrendingTag.objects.bulk_create(trending)
            
        LOGGER.info("recompute_momentum: %s trending tags", len(trending))
