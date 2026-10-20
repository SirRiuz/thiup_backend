# Django
from django.db import models

# Models
from app.models.mask import Mask
from app.models.reaction import Reaction
from app.models.thread import Thread


class Notification(models.Model):
    """A single interaction event delivered to its recipient's inbox.

    Deliberately NOT a BaseModel: never addressed by its own URL (the FE
    deep-links via `thread.uid` instead), never individually soft-deleted
    (bulk `is_read` flips, age-based hard delete in purge_inactive) — the
    UUID `id`/`uid` pair would be pure overhead on the app's
    highest-frequency insert.
    """

    REACTION = "reaction"
    REPLY = "reply"
    PROFILE_VIEW = "profile_view"
    QR_GENERATE = "qr_generate"
    DOWNLOAD = "download"
    SHARE = "share"
    LINK_COPY = "link_copy"

    VERB_CHOICES = (
        (REACTION, "Reaction"),
        (REPLY, "Reply"),
        (PROFILE_VIEW, "Profile view"),
        (QR_GENERATE, "QR generate"),
        (DOWNLOAD, "Download"),
        (SHARE, "Share"),
        (LINK_COPY, "Link copy"),
    )

    # Verbs whose deep-link target is a Thread (`thread` set, non-null).
    # PROFILE_VIEW is the one exception — see `thread` below.
    THREAD_VERBS = (REACTION, REPLY, QR_GENERATE, DOWNLOAD, SHARE, LINK_COPY)

    recipient = models.ForeignKey(
        to=Mask, on_delete=models.CASCADE, related_name="notifications", help_text="Who this notification is for."
    )

    actor = models.ForeignKey(
        to=Mask, on_delete=models.CASCADE, related_name="+", help_text="Who triggered the notification."
    )

    verb = models.CharField(max_length=16, choices=VERB_CHOICES, db_index=True)

    # Deep-link target for every THREAD_VERBS entry. For `reaction`: the
    # reacted-to thread (the recipient's own content). For `reply`: the NEW
    # reply thread the actor created — its own `sub` FK already points back
    # to the recipient's content, so the FE reuses the existing
    # `/threads/<uid>/responses/` permalink+parents logic to show both in
    # context with no extra field. For `qr_generate`/`download`/`share`/
    # `link_copy`: the thread the actor generated a QR for / downloaded the
    # QR image of / shared / copied the link of.
    # Null ONLY for `profile_view`, whose target isn't a thread at all —
    # `actor` already IS that target (the mask who viewed the recipient's
    # profile), so no extra target field is needed for it.
    thread = models.ForeignKey(to=Thread, on_delete=models.CASCADE, null=True, blank=True)

    reaction = models.ForeignKey(
        to=Reaction, on_delete=models.CASCADE, null=True, blank=True, help_text="Set only for `reaction` verb."
    )

    is_read = models.BooleanField(default=False, db_index=True)

    create_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["recipient", "-create_at"], name="notif_recipient_created_idx"),
            models.Index(fields=["recipient", "is_read"], name="notif_recipient_unread_idx"),
            # Serves the `profile_view` 24h-per-actor cooldown check
            # (app/rest/batch.py::_profile_view_allowed) — filters on the
            # exact (actor, recipient, verb) triple plus a create_at cutoff.
            models.Index(fields=["actor", "recipient", "verb", "-create_at"], name="notif_actor_recipient_verb_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.verb} · {self.actor_id} → {self.recipient_id}"
