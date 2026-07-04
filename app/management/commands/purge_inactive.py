# Python
import time
import logging
from datetime import timedelta

# Django
from django.core.management.base import BaseCommand
from django.utils import timezone

# Models
from app.models.thread import Thread
from app.models.media import ThreadFile
from app.models.reaction_relation import ReactionRelation
from app.models.tag import Tag
from app.models.report import Report
from app.models.purge_log import PurgeLog

# Signals
from app.signals.media_signals import storage_cleanup_paused

# Libs
# Module import (not the symbol): the backend is resolved at CALL time, so
# late configuration and test patching of get_backend both take effect.
from app.methods import storage_backends

LOGGER = logging.getLogger(__name__)


# Garbage-collection registry — OPT-IN on purpose. Only leaf/user-content
# entities whose hard deletion is always safe belong here. Deliberately
# excluded (adding them would be destructive, not garbage collection):
#   - Mask: Thread.mask is CASCADE — purging an inactive mask nukes its content.
#   - Reaction (catalog): seeded fixture; CASCADE would erase reaction history.
#   - MomentumLog / TrendingTag: owned and rewritten by recompute_momentum.
#   - honeypot.BlackList / LoginAttempt: forensics; deleting un-blacklists IPs.
# Ordered parents-first so cascades absorb children within the same run
# (a purged Thread already takes its files/reactions/tags/reports with it).
PURGE_MODELS = (Thread, ThreadFile, ReactionRelation, Tag, Report)


class Command(BaseCommand):
    help = (
        "Garbage collector: hard-delete rows soft-deleted (is_active=False) "
        "at least --min-age-hours ago, capped at --limit rows per run "
        "(oldest first, cascades not counted). I/O-frugal: ONE pk-select and "
        "ONE delete per model, and every doomed storage object (direct or via "
        "cascade) is removed with ONE batched DeleteObjects request. An "
        "external scheduler runs it every 2 days (EventBridge Scheduler in "
        "prod); manually: make purge_inactive"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help=(
                "Max rows to delete per run, across all models (oldest "
                "first). Also keeps the storage batch within DeleteObjects' "
                "1000-key single-request maximum."
            ),
        )
        parser.add_argument(
            "--min-age-hours",
            type=int,
            default=24,
            help=(
                "Only purge rows INACTIVE for at least this long (update_at "
                "cutoff). Protects in-flight presigned uploads, which are "
                "created inactive by design and activated on confirm."
            ),
        )

    def handle(self, *args, **options):
        started = time.monotonic()
        budget = options["limit"]
        cutoff = timezone.now() - timedelta(hours=options["min_age_hours"])
        LOGGER.info(
            "purge_inactive: starting (limit=%s, min_age=%sh)",
            budget,
            options["min_age_hours"],
        )

        total_selected = 0
        total_deleted = 0
        doomed_keys = []
        breakdown = {}
        # Each run leaves its record in PurgeLog (read-only log in the admin):
        # success with its metrics, or the error if the sweep blew up — and in
        # that case it is re-raised so the scheduler marks the task as failed.
        try:
            # The per-row storage signal is paused: this command batches the
            # storage cleanup itself (keys collected BEFORE each delete), and
            # without listeners Django can fast-delete the querysets.
            with storage_cleanup_paused():
                for model in PURGE_MODELS:
                    if budget <= 0:
                        break
                    selected, deleted, keys = self._purge_model(
                        model, cutoff, budget)
                    if selected:
                        breakdown[model.__name__] = {
                            "selected": selected,
                            "deleted": deleted,
                        }
                    budget -= selected
                    total_selected += selected
                    total_deleted += deleted
                    doomed_keys.extend(keys)

            # ONE batched storage request for every object the run doomed —
            # direct ThreadFile purges and thread-cascade casualties alike.
            # DB first, storage second: a storage hiccup leaves cheap orphaned
            # objects (logged inside delete_objects), never resurrected rows.
            removed = (
                storage_backends.get_backend().delete_objects(doomed_keys)
                if doomed_keys
                else 0
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            # .exception includes the full traceback in the log.
            LOGGER.exception(
                "purge_inactive: FAILED after %sms", duration_ms)
            PurgeLog.objects.create(
                row_limit=options["limit"],
                min_age_hours=options["min_age_hours"],
                selected_count=total_selected,
                deleted_count=total_deleted,
                files_total=len(doomed_keys),
                breakdown=breakdown,
                duration_ms=duration_ms,
                was_successful=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        elapsed = time.monotonic() - started
        PurgeLog.objects.create(
            row_limit=options["limit"],
            min_age_hours=options["min_age_hours"],
            selected_count=total_selected,
            deleted_count=total_deleted,
            files_total=len(doomed_keys),
            files_removed=removed,
            breakdown=breakdown,
            duration_ms=int(elapsed * 1000),
            was_successful=True,
        )
        LOGGER.info(
            "purge_inactive: OK — %s rows selected, %s deleted with "
            "cascades, %s/%s storage objects removed, %.2fs.",
            total_selected,
            total_deleted,
            removed,
            len(doomed_keys),
            elapsed,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"purge_inactive: {total_selected} rows selected, "
                f"{total_deleted} deleted with cascades, "
                f"{removed}/{len(doomed_keys)} storage objects removed, "
                f"{elapsed:.2f}s."
            )
        )

    def _purge_model(self, model, cutoff, budget: int) -> tuple:
        """Delete up to `budget` inactive-and-old rows of `model`.

        Exactly one pk-select and one delete() call (Django expands cascades
        internally in bulk, never per row). Returns (selected, deleted, keys):
        rows picked, rows removed including cascades, and the storage keys the
        deletion dooms. `deleted` can exceed `selected` (cascades) or
        undershoot it (a row already swept by an earlier cascade in this same
        run — filter(pk__in=...) just skips the gone ones).
        """
        expired = model.objects.filter(
            is_active=False, update_at__lt=cutoff).order_by("update_at")

        # ThreadFile: pk AND file_key come from the SAME select — no second
        # query just to learn which objects to remove from storage.
        if model is ThreadFile:
            rows = list(expired.values_list("pk", "file_key")[:budget])
            pks = [pk for pk, _ in rows]
            keys = [key for _, key in rows if key]
        else:
            pks = list(expired.values_list("pk", flat=True)[:budget])
            keys = self._cascade_keys(model, pks)

        if not pks:
            return 0, 0, []

        deleted, _ = model.objects.filter(pk__in=pks).delete()

        LOGGER.info(
            "purge_inactive: %s — %s selected, %s deleted, %s files.",
            model.__name__,
            len(pks),
            deleted,
            len(keys),
        )
        return len(pks), deleted, keys

    def _cascade_keys(self, model, pks) -> list:
        """Storage keys a cascade will doom (collected BEFORE deleting).

        Thread is the only registry model whose deletion cascades into files:
        the purged threads AND their whole reply subtree (the `sub` self-FK)
        die together, so the subtree is walked breadth-first — one query per
        reply depth (UI depth is ≤2), never per row — and the keys of every
        attached file come in one final query. Other models carry no files.
        """
        if model is not Thread or not pks:
            return []
        doomed_ids = list(pks)
        frontier = pks
        while frontier:
            frontier = list(
                Thread.objects.filter(sub_id__in=frontier)
                .values_list("pk", flat=True)
            )
            doomed_ids.extend(frontier)
        return list(
            ThreadFile.objects.filter(thread_id__in=doomed_ids)
            .exclude(file_key="")
            .values_list("file_key", flat=True)
        )
