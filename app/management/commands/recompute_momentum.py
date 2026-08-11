# Python
import logging
import math
import time
from collections import defaultdict
from datetime import timedelta
from itertools import chain

# Django
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from app.constants.threads import MOMENTUM_ENGAGE_K, MOMENTUM_FRESH_TAU_HOURS
from app.models.momentum_log import MomentumLog
from app.models.reaction_relation import ReactionRelation
from app.models.tag import Tag

# Models
from app.models.thread import Thread
from app.models.trending_tag import TrendingTag

LOGGER = logging.getLogger(__name__)


# ── Momentum formula parameters ─────────────────────────────────────────
# Freshness-first, LEAKY-BUCKET engagement ("vaso de agua"): a post's base
# freshness drains on its own from its create_at, exactly like before — but
# every interaction is now its own droplet that decays from ITS OWN
# timestamp, not the post's. That is what lets a brand-new comment or
# reaction lift an old, already-drained post: the pulse is ADDED on top of
# the (possibly ~0) base freshness, instead of multiplying it.
#
#   freshness_base    = e^(-post_age_hours / MOMENTUM_FRESH_TAU_HOURS)
#   pulse             = Σ e^(-reactor_event_age_hours    / MOMENTUM_FRESH_TAU_HOURS)
#                      + Σ e^(-commenter_event_age_hours  / MOMENTUM_FRESH_TAU_HOURS) × COMMENTER_WEIGHT
#                      + Σ e^(-author_reply_event_age_hours / MOMENTUM_FRESH_TAU_HOURS) × AUTHOR_REPLY_WEIGHT
#   momentum_score    = freshness_base + MOMENTUM_ENGAGE_K × ln(1 + pulse)
#
# Each Σ is over DISTINCT masks (one droplet per person, their MOST RECENT
# event if they interacted more than once) — same golden counting rule as
# before, now carrying a timestamp instead of just a count. log1p on the
# pulse keeps the same diminishing-returns guarantee the old formula had on
# raw counts: a simultaneous pile-up of interactions still can't let one
# post blow out the whole ranking, and a quiet post with zero pulse just
# falls back to freshness_base (unchanged from before).
#
# MOMENTUM_FRESH_TAU_HOURS/MOMENTUM_ENGAGE_K live in app/constants/threads.py
# with the other feed tuning knobs (region/affinity/proximity boosts).
#
# Golden counting rule: each signal measures DISTINCT MASKS and EXCLUDES the
# thread author (one person = one vote, nobody votes for themselves).
COMMENTER_WEIGHT = 3
AUTHOR_REPLY_WEIGHT = 5

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
        "Recompute momentum_score and the For You counters for the root "
        "posts inside the active window (default 30 days). Idempotent and "
        "cheap: 3 aggregate queries + batched bulk_update. An external "
        "scheduler runs it every 10 min (EventBridge Scheduler in prod, the "
        "`momentum` docker-compose service locally); also invocable by hand "
        "for debug or backfill: make recompute_momentum"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help=(
                "Ventana activa en días. Posts más viejos no se recalculan: "
                "a 30 días la frescura e^(-720/8) es cero para todo efecto "
                "práctico, no vale el cómputo."
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
            "recompute_momentum: iniciando reconteo (ventana=%sd, batch=%s)",
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
                f"recompute_momentum: {processed} posts en ventana "
                f"({options['days']}d), {updated} actualizados, "
                f"{elapsed:.2f}s."
            )
        )

    def _recompute(self, options) -> tuple:
        """Performs the recount and returns (processed, updated)."""
        now = timezone.now()
        window_start = now - timedelta(days=options["days"])
        batch_size = options["batch_size"]

        def decay(event_at) -> float:
            age_hours = (now - event_at).total_seconds() / 3600
            return math.exp(-age_hours / MOMENTUM_FRESH_TAU_HOURS)

        # Each signal excludes the author; if the thread has no mask (null
        # anonymous author), nobody is "the author" and all masks are counted.
        # The explicit isnull prevents NOT(mask = NULL) from discarding rows.

        # 1/3 — Reactors per post: DISTINCT mask ≠ author, keeping each
        #       mask's MOST RECENT reaction timestamp (toggle semantics mean
        #       a mask has at most one active reaction per thread, but the
        #       dedup-by-latest is kept as a safety net rather than assumed).
        reactor_events = (
            ReactionRelation.objects.filter(
                is_active=True,
                thread__is_active=True,
                thread__sub__isnull=True,
                thread__create_at__gte=window_start,
            )
            .filter(Q(thread__mask__isnull=True) | ~Q(mask=F("thread__mask")))
            .values_list("thread_id", "mask_id", "create_at")
        )
        reactor_latest = {}
        for thread_id, mask_id, event_at in reactor_events.iterator():
            key = (thread_id, mask_id)
            if key not in reactor_latest or event_at > reactor_latest[key]:
                reactor_latest[key] = event_at

        unique_reactors = defaultdict(int)
        reactor_pulse = defaultdict(float)
        for (thread_id, _mask_id), event_at in reactor_latest.items():
            unique_reactors[thread_id] += 1
            reactor_pulse[thread_id] += decay(event_at)

        # 2/3 — Unique commenters per post: DISTINCT masks ≠ author at
        #       depth 1 (comments) and 2 (replies to comments) —
        #       what the UI generates. 100 comments from 1 person = 1; a
        #       thread where only the author talks = 0. (root, mask) pairs
        #       from both levels merge into a {root: {mask: latest_at}} map
        #       so the same person commenting at both levels counts ONCE,
        #       using their most recent comment as that droplet's timestamp.
        depth1_events = (
            Thread.objects.filter(
                is_active=True,
                mask__isnull=False,
                sub__isnull=False,
                sub__sub__isnull=True,
                sub__is_active=True,
                sub__create_at__gte=window_start,
            )
            .filter(Q(sub__mask__isnull=True) | ~Q(mask=F("sub__mask")))
            .values_list("sub_id", "mask_id", "create_at")
        )

        depth2_events = (
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
            .values_list("sub__sub_id", "mask_id", "create_at")
        )

        commenter_latest = defaultdict(dict)
        for root_id, mask_id, event_at in chain(depth1_events.iterator(), depth2_events.iterator()):
            bucket = commenter_latest[root_id]
            if mask_id not in bucket or event_at > bucket[mask_id]:
                bucket[mask_id] = event_at

        unique_commenters = {root_id: len(masks) for root_id, masks in commenter_latest.items()}
        commenter_pulse = {
            root_id: sum(decay(event_at) for event_at in masks.values()) for root_id, masks in commenter_latest.items()
        }

        # 3/3 — Author dialogue: DISTINCT people (≠ author) the author
        #       replied to (author reply, depth 2, to a direct comment from
        #       another person), each droplet timestamped by the author's
        #       reply (their most recent one to that person). Self-replies
        #       don't count.
        author_reply_events = (
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
            .values_list("sub__sub_id", "sub__mask", "create_at")
        )
        author_reply_latest = defaultdict(dict)
        for root_id, interlocutor_mask_id, event_at in author_reply_events.iterator():
            bucket = author_reply_latest[root_id]
            if interlocutor_mask_id not in bucket or event_at > bucket[interlocutor_mask_id]:
                bucket[interlocutor_mask_id] = event_at

        author_reply_pulse = {
            root_id: sum(decay(event_at) for event_at in interlocutors.values())
            for root_id, interlocutors in author_reply_latest.items()
        }

        # Score per post, in Python (exp/log1p are not done in SQL nor per
        # request: only here, once per run).
        roots = Thread.objects.filter(
            is_active=True,
            sub__isnull=True,
            create_at__gte=window_start,
        ).only("id", "create_at", *UPDATED_FIELDS)

        pending, processed, updated = [], 0, 0
        for thread in roots.iterator(chunk_size=batch_size):
            processed += 1
            reactors = unique_reactors.get(thread.id, 0)
            commenter_count = unique_commenters.get(thread.id, 0)
            # views: no counter in the model → term omitted (v1).

            pulse = (
                reactor_pulse.get(thread.id, 0.0)
                + commenter_pulse.get(thread.id, 0.0) * COMMENTER_WEIGHT
                + author_reply_pulse.get(thread.id, 0.0) * AUTHOR_REPLY_WEIGHT
            )
            freshness_base = decay(thread.create_at)
            momentum = freshness_base + MOMENTUM_ENGAGE_K * math.log1p(pulse)

            # Skip no-changes: the dead tail (pulse=0, freshness_base=0) is
            # not rewritten — fewer writes on each cron run.
            if (
                thread.momentum_score == momentum
                and thread.unique_reactors_count == reactors
                and thread.unique_commenters_count == commenter_count
            ):
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
        from django.db.models import Count, Sum

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
