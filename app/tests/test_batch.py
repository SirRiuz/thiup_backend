# Python
import hashlib
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import patch

# Django
from django.test import Client, TestCase, override_settings
from rest_framework import status

from app.constants.engagement import BATCH_MAX_EVENTS
from app.methods.tokens import encode_token, issue_ticket

# Models
from app.models.engagement_daily import EngagementDaily
from app.models.mask import Mask
from app.models.notification import Notification
from app.tests.test_foryou import make_mask, make_thread

client = Client()

# The Django test client hits the API from 127.0.0.1, so MaskMiddleware
# derives THIS mask as the ACTOR of every batch below (same convention as
# test_notifications.py).
CLIENT_MASK_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()


def _token() -> str:
    return encode_token({"timestamp": datetime.now().__str__()})


def _post_batch(events, **extra):
    return client.post(
        "/batch/",
        {"events": events},
        content_type="application/json",
        HTTP_CLIENT_ASSERTION=_token(),
        **extra,
    )


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class BatchIngestTest(TestCase):
    def test_duplicate_events_collapse_into_one_summed_row(self):
        response = _post_batch(
            [
                {"target_type": "thread", "target_uid": "abc123", "type": "qr", "count": 2},
                {"target_type": "thread", "target_uid": "abc123", "type": "qr", "count": 3},
            ]
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data, {"status": "ok"})
        self.assertEqual(EngagementDaily.objects.count(), 1)
        row = EngagementDaily.objects.get()
        self.assertEqual(row.qr_count, 5)

    def test_second_batch_same_day_increments_in_place(self):
        _post_batch([{"target_type": "thread", "target_uid": "abc123", "type": "download", "count": 1}])
        _post_batch([{"target_type": "thread", "target_uid": "abc123", "type": "download", "count": 4}])

        self.assertEqual(EngagementDaily.objects.count(), 1)
        self.assertEqual(EngagementDaily.objects.get().download_count, 5)

    def test_different_day_creates_a_second_row(self):
        # A REAL datetime, not a bare MagicMock: `app.rest.batch.timezone` IS
        # the shared `django.utils.timezone` module, so a MagicMock leaks
        # into every unrelated `timezone.now()` call made during the request
        # (logging, etc.) and breaks things far outside this test.
        day_one = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        day_two = datetime(2026, 1, 2, tzinfo=dt_timezone.utc)
        with patch("app.rest.batch.timezone.now") as mock_now:
            mock_now.return_value = day_one
            _post_batch([{"target_type": "mask", "target_uid": "a1b2c3", "type": "view", "count": 1}])
            mock_now.return_value = day_two
            _post_batch([{"target_type": "mask", "target_uid": "a1b2c3", "type": "view", "count": 1}])

        self.assertEqual(EngagementDaily.objects.count(), 2)

    def test_oversized_batch_rejected(self):
        events = [{"target_type": "thread", "target_uid": "abc123", "type": "view", "count": 1}] * (
            BATCH_MAX_EVENTS + 1
        )
        response = _post_batch(events)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(EngagementDaily.objects.count(), 0)

    def test_invalid_type_rejected(self):
        response = _post_batch([{"target_type": "thread", "target_uid": "abc123", "type": "not_a_type", "count": 1}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_target_type_rejected(self):
        response = _post_batch([{"target_type": "user", "target_uid": "abc123", "type": "view", "count": 1}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_multiple_types_same_target_merge_into_one_row(self):
        """Two different event types for the SAME target must land in one
        VALUES row — Postgres forbids ON CONFLICT DO UPDATE from touching
        the same conflict-key row twice within one statement."""
        response = _post_batch(
            [
                {"target_type": "thread", "target_uid": "abc123", "type": "qr", "count": 1},
                {"target_type": "thread", "target_uid": "abc123", "type": "download", "count": 1},
            ]
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(EngagementDaily.objects.count(), 1)
        row = EngagementDaily.objects.get()
        self.assertEqual((row.qr_count, row.download_count), (1, 1))

    def test_write_is_o1_regardless_of_batch_size(self):
        """The whole batch write is ONE query (the upsert), whether it
        carries 1 event or the max 200 — no N+1."""
        # Warm the mask LocMem cache (conftest clears it every test) so the
        # measured requests don't pay the one-time mask lookup.
        _post_batch([{"target_type": "thread", "target_uid": "warmup", "type": "view", "count": 1}])

        with self.assertNumQueries(1):
            _post_batch([{"target_type": "thread", "target_uid": "one-event", "type": "view", "count": 1}])

        many_events = [
            {"target_type": "thread", "target_uid": f"uid{i:03d}", "type": "view", "count": 1}
            for i in range(BATCH_MAX_EVENTS)
        ]
        with self.assertNumQueries(1):
            _post_batch(many_events)


@override_settings(SINGLE_REQUEST_PROTECT=True, ENCRYPTED_RESPONSE=False)
class BatchTicketRequirementTest(TestCase):
    def test_requires_ticket(self):
        response = client.post(
            "/batch/",
            {"events": [{"target_type": "thread", "target_uid": "abc123", "type": "view", "count": 1}]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_succeeds_with_a_valid_ticket(self):
        response = client.post(
            "/batch/",
            {"events": [{"target_type": "thread", "target_uid": "abc123", "type": "view", "count": 1}]},
            content_type="application/json",
            HTTP_CLIENT_ASSERTION=issue_ticket(),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class BatchNoCaptchaTest(TestCase):
    """No @human_validator on this endpoint — it must work even when the
    captcha gate is globally on, without any X-Human-Pass header (matches
    foryou/closeyou: fire-and-forget telemetry, not content creation)."""

    @override_settings(
        CAPTCHA_PROTECT=True,
        CAP_SITEVERIFY_URL="http://cap.test",
        CAP_PUBLIC_URL="http://cap.test",
        CAP_SITE_KEY="site-key",
        CAP_SECRET="top-secret",
    )
    def test_works_without_human_pass_even_when_captcha_is_on(self):
        response = _post_batch([{"target_type": "thread", "target_uid": "abc123", "type": "view", "count": 1}])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class BatchEngagementNotificationTest(TestCase):
    """POST /batch/ also creates actor-identified Notification rows for a
    subset of engagement types — see app/rest/batch.py::_notify_engagement.
    An accepted, deliberate departure from EngagementDaily's "anonymous
    aggregate only" design (see CLAUDE.md's "In-app notifications" section)."""

    def setUp(self):
        self.actor, _ = Mask.objects.get_or_create(hash=CLIENT_MASK_HASH)
        self.owner = make_mask("owner")

    def test_download_notifies_the_thread_owner(self):
        thread = make_thread(self.owner)
        _post_batch([{"target_type": "thread", "target_uid": thread.uid, "type": "download", "count": 1}])

        notif = Notification.objects.get()
        self.assertEqual(notif.recipient_id, self.owner.id)
        self.assertEqual(notif.actor_id, self.actor.id)
        self.assertEqual(notif.verb, Notification.DOWNLOAD)
        self.assertEqual(notif.thread_id, thread.id)

    def test_share_qr_and_link_copy_each_notify_independently(self):
        thread = make_thread(self.owner)
        _post_batch(
            [
                {"target_type": "thread", "target_uid": thread.uid, "type": "share", "count": 1},
                {"target_type": "thread", "target_uid": thread.uid, "type": "qr", "count": 1},
                {"target_type": "thread", "target_uid": thread.uid, "type": "link_copy", "count": 1},
            ]
        )

        verbs = set(Notification.objects.values_list("verb", flat=True))
        self.assertEqual(verbs, {Notification.SHARE, Notification.QR_GENERATE, Notification.LINK_COPY})

    def test_thread_view_never_notifies(self):
        """`view` on a THREAD target stays purely anonymous — only a mask
        (profile) view is notify-eligible; nothing tracks a thread `view`
        event today anyway, but this locks in the intent even if something
        starts sending one."""
        thread = make_thread(self.owner)
        _post_batch([{"target_type": "thread", "target_uid": thread.uid, "type": "view", "count": 5}])

        self.assertEqual(Notification.objects.count(), 0)

    def test_engagement_on_own_thread_does_not_notify(self):
        thread = make_thread(self.actor)
        _post_batch([{"target_type": "thread", "target_uid": thread.uid, "type": "download", "count": 1}])

        self.assertEqual(Notification.objects.count(), 0)

    def test_engagement_on_nonexistent_thread_is_a_harmless_no_op(self):
        """Unlike the EngagementDaily upsert (target existence unvalidated
        by design), notification creation DOES resolve the real Thread —
        a target that doesn't exist just skips silently, no error."""
        response = _post_batch([{"target_type": "thread", "target_uid": "doesnotexist", "type": "share", "count": 1}])

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Notification.objects.count(), 0)

    def test_profile_view_notifies_the_mask_owner(self):
        _post_batch([{"target_type": "mask", "target_uid": self.owner.hash[:6], "type": "view", "count": 1}])

        notif = Notification.objects.get()
        self.assertEqual(notif.recipient_id, self.owner.id)
        self.assertEqual(notif.actor_id, self.actor.id)
        self.assertEqual(notif.verb, Notification.PROFILE_VIEW)
        self.assertIsNone(notif.thread_id)

    def test_own_profile_view_does_not_notify(self):
        _post_batch([{"target_type": "mask", "target_uid": self.actor.hash[:6], "type": "view", "count": 1}])
        self.assertEqual(Notification.objects.count(), 0)

    def test_profile_view_cooldown_suppresses_a_second_notification_within_24h(self):
        _post_batch([{"target_type": "mask", "target_uid": self.owner.hash[:6], "type": "view", "count": 3}])
        _post_batch([{"target_type": "mask", "target_uid": self.owner.hash[:6], "type": "view", "count": 1}])

        self.assertEqual(Notification.objects.filter(verb=Notification.PROFILE_VIEW).count(), 1)

    def test_profile_view_cooldown_is_per_actor_not_per_recipient(self):
        """A DIFFERENT viewer within the same 24h window still notifies —
        the cooldown is keyed on (actor, recipient), not recipient alone."""
        _post_batch([{"target_type": "mask", "target_uid": self.owner.hash[:6], "type": "view", "count": 1}])

        second_actor_response = client.post(
            "/batch/",
            {"events": [{"target_type": "mask", "target_uid": self.owner.hash[:6], "type": "view", "count": 1}]},
            content_type="application/json",
            HTTP_CLIENT_ASSERTION=_token(),
            REMOTE_ADDR="10.0.0.9",
        )

        self.assertEqual(second_actor_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Notification.objects.filter(verb=Notification.PROFILE_VIEW).count(), 2)
