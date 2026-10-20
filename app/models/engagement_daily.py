# Django
from django.db import models


class EngagementDaily(models.Model):
    """Daily rollup counter for passive engagement (views, QR, downloads, shares).

    Foundation for a future analytics system — deliberately generic and
    extensible. Deliberately NOT a BaseModel (never addressed individually,
    upserted at high frequency via a single `INSERT ... ON CONFLICT`) and
    deliberately NOT a ForeignKey to Thread/Mask: `target_uid` is stored raw
    so the batch upsert never needs a join/lookup, and target existence is
    NOT validated on write — these are accepted as best-effort/approximate
    stats, consistent with every major platform's "view count".
    """

    THREAD = "thread"
    MASK = "mask"

    TARGET_TYPE_CHOICES = (
        (THREAD, "Thread"),
        (MASK, "Mask"),
    )

    target_type = models.CharField(max_length=8, choices=TARGET_TYPE_CHOICES)

    # Raw public identifier, not a FK: Thread.uid (12 chars) or a Mask's
    # 6-hex public id (`hash[:6]`, same identifier used everywhere a mask is
    # publicly referenced — never the full 64-char hash).
    target_uid = models.CharField(max_length=12)

    # Always server-computed (timezone.now().date()), never trusted from
    # the client — the day boundary is what the UNIQUE constraint upserts on.
    day = models.DateField()

    view_count = models.PositiveIntegerField(default=0)
    qr_count = models.PositiveIntegerField(default=0)
    link_copy_count = models.PositiveIntegerField(default=0)
    download_count = models.PositiveIntegerField(default=0)
    share_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["target_type", "target_uid", "day"], name="unique_engagement_per_target_day"
            ),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_uid", "-day"], name="engagement_trend_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.target_type}:{self.target_uid} · {self.day}"
