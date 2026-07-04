# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


class PurgeLog(BaseModel):
    """
    Log of each garbage-collector run (purge_inactive).

    Written only by the management command `purge_inactive` (run by the
    external scheduler every 2 days) — the admin exposes it read-only, with no
    creation or editing (see PurgeLogAdmin). Used to audit that the GC runs,
    what it swept (per model), how many storage objects it removed and whether
    it failed.
    """

    row_limit = models.PositiveIntegerField(
        default=0,
        help_text="Row cap used for the run (--limit).",
    )

    min_age_hours = models.PositiveIntegerField(
        default=0,
        help_text="Inactivity age threshold used for the run (--min-age-hours).",
    )

    selected_count = models.PositiveIntegerField(
        default=0,
        help_text="Soft-deleted rows picked by the scan (counts against the cap).",
    )

    deleted_count = models.PositiveIntegerField(
        default=0,
        help_text="Rows actually removed, cascades included.",
    )

    files_total = models.PositiveIntegerField(
        default=0,
        help_text="Storage objects doomed by the run (direct + via cascade).",
    )

    files_removed = models.PositiveIntegerField(
        default=0,
        help_text="Storage objects the batched DeleteObjects actually removed.",
    )

    breakdown = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Per-model detail: {"Thread": {"selected": n, "deleted": m}, ...} '
            "— only models that had something to purge appear."
        ),
    )

    duration_ms = models.PositiveIntegerField(
        default=0,
        help_text="Total run duration in milliseconds.",
    )

    was_successful = models.BooleanField(
        default=True,
        help_text="False if the run raised an exception.",
    )

    error = models.TextField(
        blank=True,
        default="",
        help_text="Exception message when was_successful is False.",
    )

    def __str__(self) -> str:
        status = "ok" if self.was_successful else "ERROR"
        return (
            f"{self.create_at:%Y-%m-%d %H:%M} · {status} · "
            f"{self.selected_count} selected / {self.deleted_count} deleted / "
            f"{self.files_removed} files"
        )
