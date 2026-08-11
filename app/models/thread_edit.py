# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


class ThreadEditHistory(BaseModel):
    """One row per edit of a Thread (a post or a reply — both share this table,
    same as Thread itself covers both via `sub`).

    Snapshots the TEXT as it was immediately BEFORE a given edit — the live
    `Thread.text` is always the latest version, so only superseded versions
    need to be remembered here. `create_at` (inherited from BaseModel) is this
    row's own timestamp, which doubles as "when this edit happened" — no
    separate column needed.

    Media changes are intentionally NOT snapshotted with previews (product
    decision): `purge_inactive` hard-deletes any is_active=False ThreadFile
    after 24h regardless of why it was deactivated, so keeping "removed"
    media alive for history would require GC changes that are out of scope.
    Only the COUNT of files added/removed by this specific edit is kept, so
    the UI can say "+2 files, -1 file" without resolving to a real object.
    """

    thread = models.ForeignKey(
        to="app.Thread",
        on_delete=models.CASCADE,
        related_name="edit_history",
        help_text="The thread (post or reply) this snapshot belongs to.",
    )

    previous_text = models.TextField(
        help_text="The thread's text value immediately BEFORE this edit was applied.",
    )

    media_added_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of media files attached to the thread by this specific edit.",
    )
    media_removed_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of media files removed from the thread by this specific edit.",
    )

    # Deliberately NOT in purge_inactive.PURGE_MODELS: rows here never get
    # is_active=False (nothing soft-deletes them), so the 24h GC sweep would
    # never select them anyway — on_delete=CASCADE from Thread already cleans
    # them up if the thread itself is purged. Not an oversight; don't "fix".

    def __str__(self) -> str:
        return f"edit of {self.thread_id} @ {self.create_at}"
