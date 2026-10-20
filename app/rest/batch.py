# Python
import operator
from collections import defaultdict
from datetime import timedelta
from functools import reduce

# Django
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED
from rest_framework.views import APIView

from app.models.engagement_daily import EngagementDaily
from app.models.mask import Mask
from app.models.notification import Notification
from app.models.thread import Thread
from app.permissions.client import IsClientAuthenticated
from app.permissions.throttling import TrustedIPScopedRateThrottle

# Libs
from app.rest.serializers.batch_serializer import BatchRequestSerializer

_COUNT_COLUMNS = ("view_count", "qr_count", "link_copy_count", "download_count", "share_count")
_TYPE_TO_COLUMN = {
    "view": "view_count",
    "qr": "qr_count",
    "link_copy": "link_copy_count",
    "download": "download_count",
    "share": "share_count",
}

# Which engagement columns, for a THREAD target, also earn the target's
# author a Notification — a deliberate, accepted departure from
# EngagementDaily's "anonymous aggregate only" design: these 4 are
# considered deliberate-enough actions (unlike a passive page view) to be
# worth surfacing with actor identity. `view_count` on a thread target is
# NOT in this map on purpose — nothing tracks that event today (only mask
# profile views do), and thread view counts stay purely anonymous.
_THREAD_COLUMN_TO_VERB = {
    "qr_count": Notification.QR_GENERATE,
    "download_count": Notification.DOWNLOAD,
    "share_count": Notification.SHARE,
    "link_copy_count": Notification.LINK_COPY,
}

PROFILE_VIEW_COOLDOWN_HOURS = 24


class BatchView(APIView):
    """
    POST /batch/ — ingests client-batched, pre-aggregated engagement events
    (profile views, QR generations, downloads, shares) into the
    EngagementDaily daily rollup.

    Foundation for a future analytics system: generic event shape, one
    endpoint, extensible via EVENT_TYPE_CHOICES without new routes.

    No @human_validator: this is fire-and-forget telemetry the FE flushes
    opportunistically (interval tick / tab-hide), not human-generated
    content creation — same reasoning as foryou/closeyou.

    The whole batch is written with ONE multi-row `INSERT ... ON CONFLICT
    DO UPDATE` statement, regardless of how many distinct targets/types it
    carries — this is what keeps the write O(1) queries. Django's ORM-native
    `bulk_create(update_conflicts=True)` was considered and rejected: it
    does `SET col = EXCLUDED.col` (last-write-wins overwrite), not
    `col = col + EXCLUDED.col` (additive) — it would silently DROP counts
    on a second same-day batch instead of accumulating them.

    A SECOND, separate step (`_notify_engagement`, run after the upsert
    above) creates real actor-identified Notification rows for 5 of these
    event types: `qr`/`download`/`share`/`link_copy` on a thread target, and
    `view` on a mask (profile) target. This is an accepted, deliberate
    departure from the "anonymous aggregate only" design EngagementDaily was
    built for — the tradeoff: this step is NOT O(1) queries like the upsert
    above, it's O(distinct notify-eligible targets in the batch), because
    each one needs a real Mask/Thread row resolved (existence IS validated
    here, unlike the upsert) and a real Notification.objects.create() (not
    bulk_create — the post_save signal that bumps the recipient's unread
    badge must fire per row). In practice this stays tiny: a batch reflects
    one tab's ~75s of activity, so it touches only the handful of
    threads/profile the user was actually looking at, never anywhere near
    the 200-event cap.
    """

    permission_classes = (IsClientAuthenticated,)

    def get_throttles(self) -> list:
        self.throttle_scope = "batch_ingest"
        return [TrustedIPScopedRateThrottle()]

    def post(self, request) -> Response:
        serializer = BatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Merge by (target_type, target_uid): Postgres forbids ON CONFLICT
        # DO UPDATE from touching the same conflict-key row twice within one
        # statement, so every type for a given target must land in a SINGLE
        # VALUES row, not one row per type.
        merged = defaultdict(lambda: defaultdict(int))
        for event in serializer.validated_data["events"]:
            key = (event["target_type"], event["target_uid"])
            merged[key][_TYPE_TO_COLUMN[event["type"]]] += event["count"]

        if merged:
            self._upsert(merged)
            self._notify_engagement(request, merged)

        return Response({"status": "ok"}, status=HTTP_201_CREATED)

    @staticmethod
    def _upsert(merged: dict) -> None:
        today = timezone.now().date()
        table = EngagementDaily._meta.db_table

        columns = ("target_type", "target_uid", "day", *_COUNT_COLUMNS)
        values_sql = ", ".join(f"({', '.join(['%s'] * len(columns))})" for _ in merged)
        update_sql = ", ".join(f"{col} = {table}.{col} + EXCLUDED.{col}" for col in _COUNT_COLUMNS)

        params = []
        for (target_type, target_uid), counts in merged.items():
            params.extend([target_type, target_uid, today])
            params.extend(counts.get(col, 0) for col in _COUNT_COLUMNS)

        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values_sql} "
            f"ON CONFLICT (target_type, target_uid, day) DO UPDATE SET {update_sql}"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

    def _notify_engagement(self, request, merged: dict) -> None:
        actor = request.mask

        # Only pull in targets that actually carry a notify-eligible column
        # with a positive count — a batch of pure `view` events (by far the
        # most common traffic: every thread/profile page load) must stay at
        # the upsert's 1 query, not pay a Thread/Mask lookup for nothing.
        thread_uids = {
            uid
            for (ttype, uid), counts in merged.items()
            if ttype == EngagementDaily.THREAD and any(counts.get(col, 0) > 0 for col in _THREAD_COLUMN_TO_VERB)
        }
        mask_prefixes = {
            uid
            for (ttype, uid), counts in merged.items()
            if ttype == EngagementDaily.MASK and counts.get("view_count", 0) > 0
        }
        if not thread_uids and not mask_prefixes:
            return

        threads = {}
        if thread_uids:
            threads = {t.uid: t for t in Thread.objects.filter(uid__in=thread_uids, is_active=True, visibility=True)}

        # target_uid for a mask is a 6-hex PREFIX of the full hash (same
        # public id used everywhere, see masks.py's _PUBLIC_ID_RE) — not a
        # direct lookup key. On the astronomically-rare prefix collision the
        # OLDEST mask wins, same deterministic rule as MasksViewSet.retrieve.
        masks = {}
        if mask_prefixes:
            prefix_q = reduce(operator.or_, (Q(hash__startswith=p) for p in mask_prefixes))
            for m in Mask.objects.filter(prefix_q, is_active=True).order_by("create_at"):
                masks.setdefault(m.hash[:6], m)

        for (target_type, target_uid), counts in merged.items():
            if target_type == EngagementDaily.THREAD:
                self._notify_thread_engagement(actor, threads.get(target_uid), counts)
            elif target_type == EngagementDaily.MASK:
                self._notify_profile_view(actor, masks.get(target_uid), counts)

    @staticmethod
    def _notify_thread_engagement(actor: Mask, thread, counts: dict) -> None:
        if thread is None or thread.mask_id == actor.id:
            return
        for column, verb in _THREAD_COLUMN_TO_VERB.items():
            if counts.get(column, 0) > 0:
                Notification.objects.create(recipient_id=thread.mask_id, actor=actor, verb=verb, thread=thread)

    @staticmethod
    def _notify_profile_view(actor: Mask, mask, counts: dict) -> None:
        if mask is None or mask.id == actor.id or counts.get("view_count", 0) <= 0:
            return
        cutoff = timezone.now() - timedelta(hours=PROFILE_VIEW_COOLDOWN_HOURS)
        already_notified_today = Notification.objects.filter(
            verb=Notification.PROFILE_VIEW, actor=actor, recipient_id=mask.id, create_at__gte=cutoff
        ).exists()
        if already_notified_today:
            return
        Notification.objects.create(recipient_id=mask.id, actor=actor, verb=Notification.PROFILE_VIEW, thread=None)
