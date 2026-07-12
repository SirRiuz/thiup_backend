# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel
from app.models.mask import Mask
from app.models.thread import Thread


class Report(BaseModel):
    """Anonymous report of a thread.

    Anonymity: `reporter` is the project's pseudonymous Mask (SHA-256 of the
    client IP) — the SAME ephemeral identifier ReactionRelation uses, NEVER a
    real identity. It exists only to enforce "one report per user per thread"
    (unique constraint, enabling an upsert on re-report). No personal data is
    stored, and the admin never reveals who the person is.
    """

    SPAM = "spam_or_deception"
    HARASSMENT = "harassment"
    HATE = "hate_speech"
    DANGEROUS = "dangerous_or_self_harm"
    MINORS = "minors"
    OTHER = "other"

    CATEGORY_CHOICES = (
        (SPAM, "Spam or deception"),
        (HARASSMENT, "Harassment or bullying"),
        (HATE, "Hate speech"),
        (DANGEROUS, "Dangerous content or self-harm"),
        (MINORS, "Content involving minors"),
        (OTHER, "Other"),
    )

    REASON_MAX_LENGTH = 300

    thread = models.ForeignKey(
        to=Thread, on_delete=models.CASCADE, related_name="reports", help_text="Reported thread."
    )

    # Pseudonymous reporter (Mask) — NOT a real identity (see class docstring).
    reporter = models.ForeignKey(
        to=Mask, on_delete=models.CASCADE, help_text="Pseudonymous mask of the reporter (anonymity key)."
    )

    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES, db_index=True)

    reason = models.CharField(
        max_length=REASON_MAX_LENGTH,
        blank=True,
        default="",
        help_text="Optional free-text detail (used for the 'other' category).",
    )

    # Flags reports that need a SEPARATE, high-priority review path. Set True
    # when category == MINORS.
    # TODO(moderation): "minors" reports are legally critical — when a real
    # moderation system exists, route these to a DEDICATED priority/legal review
    # workflow, NOT the normal moderation queue. This flag only marks them.
    is_priority = models.BooleanField(default=False, db_index=True)

    class Meta:
        constraints = [
            # One report per pseudonymous user per thread → re-report = upsert.
            models.UniqueConstraint(fields=["thread", "reporter"], name="unique_report_per_thread_reporter"),
        ]
        indexes = [
            # Moderation changelist: priority first, newest first.
            models.Index(fields=["is_priority", "-create_at"], name="report_priority_idx"),
        ]
