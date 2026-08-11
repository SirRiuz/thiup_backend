# Python
import hashlib
from datetime import datetime

# Django
from django.test import Client, TestCase, override_settings
from rest_framework import status

from app.methods.tokens import encode_token

# Models
from app.models.mask import Mask
from app.models.notification import Notification
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation

# Serializers
from app.rest.serializers.reaction_serializer import ReactionRelationSerializer
from app.tests.test_foryou import make_mask, make_thread

client = Client()

# The Django test client hits the API from 127.0.0.1, so MaskMiddleware
# derives THIS mask for every request (same convention as test_captcha.py).
CLIENT_MASK_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()


def _token() -> str:
    return encode_token({"timestamp": datetime.now().__str__()})


# CRUD/signal tests use plain JSON, so the transport layers are pinned off
# (same convention as test_reaction_view.py) — the encrypted/ticket paths are
# covered elsewhere.
@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class NotificationSignalsTest(TestCase):
    def setUp(self):
        Reaction.objects.all().delete()
        self.reaction_a = Reaction.objects.create(name="love", emoji="❤️")
        self.reaction_b = Reaction.objects.create(name="laugh", emoji="😂")
        self.author = make_mask("author")
        self.reactor = make_mask("reactor")

    def test_reaction_creates_notification_for_recipient(self):
        thread = make_thread(self.author)
        ReactionRelation.objects.create(mask=self.reactor, thread=thread, reaction=self.reaction_a)

        notif = Notification.objects.get()
        self.assertEqual(notif.recipient_id, self.author.id)
        self.assertEqual(notif.actor_id, self.reactor.id)
        self.assertEqual(notif.verb, Notification.REACTION)
        self.assertEqual(notif.thread_id, thread.id)
        self.assertEqual(notif.reaction_id, self.reaction_a.id)
        self.author.refresh_from_db()
        self.assertEqual(self.author.unread_notifications_count, 1)

    def test_self_reaction_creates_no_notification(self):
        thread = make_thread(self.author)
        ReactionRelation.objects.create(mask=self.author, thread=thread, reaction=self.reaction_a)

        self.assertEqual(Notification.objects.count(), 0)
        self.author.refresh_from_db()
        self.assertEqual(self.author.unread_notifications_count, 0)

    def test_reaction_toggle_off_creates_no_notification(self):
        thread = make_thread(self.author)
        serializer = ReactionRelationSerializer()
        serializer.create({"mask": self.reactor, "thread": thread, "reaction": self.reaction_a})
        serializer.create({"mask": self.reactor, "thread": thread, "reaction": self.reaction_a})  # toggle off

        self.assertEqual(Notification.objects.count(), 1)
        self.author.refresh_from_db()
        self.assertEqual(self.author.unread_notifications_count, 1)

    def test_reaction_switch_creates_second_notification(self):
        """Switching emoji (not toggle-off) is a real new row — intentional,
        see app/signals/notification_signals.py."""
        thread = make_thread(self.author)
        serializer = ReactionRelationSerializer()
        serializer.create({"mask": self.reactor, "thread": thread, "reaction": self.reaction_a})
        serializer.create({"mask": self.reactor, "thread": thread, "reaction": self.reaction_b})

        self.assertEqual(Notification.objects.count(), 2)
        self.author.refresh_from_db()
        self.assertEqual(self.author.unread_notifications_count, 2)

    def test_reply_creates_notification_for_parent_author(self):
        parent = make_thread(self.author)
        reply = make_thread(self.reactor, sub=parent)

        notif = Notification.objects.get()
        self.assertEqual(notif.recipient_id, self.author.id)
        self.assertEqual(notif.actor_id, self.reactor.id)
        self.assertEqual(notif.verb, Notification.REPLY)
        self.assertEqual(notif.thread_id, reply.id)
        self.author.refresh_from_db()
        self.assertEqual(self.author.unread_notifications_count, 1)

    def test_self_reply_creates_no_notification(self):
        parent = make_thread(self.author)
        make_thread(self.author, sub=parent)

        self.assertEqual(Notification.objects.count(), 0)

    def test_root_thread_creation_creates_no_notification(self):
        make_thread(self.author)
        self.assertEqual(Notification.objects.count(), 0)

    def test_admin_created_notification_bumps_the_badge_too(self):
        """A row created directly (e.g. from the Django admin's add form,
        or any other future code path) is not routed through _notify() —
        the badge bump lives on Notification.post_save itself so it fires
        identically regardless of how the row came to exist."""
        thread = make_thread(self.author)
        reaction = self.reaction_a

        Notification.objects.create(
            recipient=self.author,
            actor=self.reactor,
            verb=Notification.REACTION,
            thread=thread,
            reaction=reaction,
        )

        self.author.refresh_from_db()
        self.assertEqual(self.author.unread_notifications_count, 1)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class NotificationApiTest(TestCase):
    def setUp(self):
        Reaction.objects.all().delete()
        self.reaction = Reaction.objects.create(name="love", emoji="❤️")
        self.me, _ = Mask.objects.get_or_create(hash=CLIENT_MASK_HASH)
        self.other = make_mask("other")

    def test_unread_count_reflects_counter(self):
        thread = make_thread(self.me)
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)

        response = client.get("/notifications/unread-count/", HTTP_X_DYNAMIC_TOKEN=_token())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unread_count"], 1)

    def test_mark_read_resets_counter_and_flips_rows(self):
        thread = make_thread(self.me)
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)

        response = client.post("/notifications/mark-read/", HTTP_X_DYNAMIC_TOKEN=_token())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"status": "ok"})
        self.me.refresh_from_db()
        self.assertEqual(self.me.unread_notifications_count, 0)
        self.assertFalse(Notification.objects.filter(is_read=False).exists())

    def test_unread_count_is_not_stale_from_the_mask_cache(self):
        """MaskMiddleware caches the whole Mask instance for 60s (assumed
        immutable outside country_code) — the notification signal fires on
        the ACTOR's request, not the recipient's, so without explicit cache
        invalidation the recipient's ALREADY-CACHED instance would keep
        answering the PRE-increment count. Regression test for that bug."""
        thread = make_thread(self.me)
        # Populate the LocMem cache for `self.me` BEFORE the notification-
        # worthy interaction happens — this is the request whose cached
        # Mask instance would otherwise go stale.
        warmup = client.get("/notifications/unread-count/", HTTP_X_DYNAMIC_TOKEN=_token())
        self.assertEqual(warmup.data["unread_count"], 0)

        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)

        response = client.get("/notifications/unread-count/", HTTP_X_DYNAMIC_TOKEN=_token())
        self.assertEqual(response.data["unread_count"], 1)

    def test_mark_read_invalidates_the_cached_mask_too(self):
        """Same staleness risk in the other direction: without invalidating
        on mark-read, a request that already cached the PRE-reset count
        would keep reporting it as unread for up to 60s."""
        thread = make_thread(self.me)
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)
        # This GET caches the Mask instance WITH unread_count == 1.
        before = client.get("/notifications/unread-count/", HTTP_X_DYNAMIC_TOKEN=_token())
        self.assertEqual(before.data["unread_count"], 1)

        client.post("/notifications/mark-read/", HTTP_X_DYNAMIC_TOKEN=_token())

        after = client.get("/notifications/unread-count/", HTTP_X_DYNAMIC_TOKEN=_token())
        self.assertEqual(after.data["unread_count"], 0)

    def test_dismiss_deletes_the_notification(self):
        thread = make_thread(self.me)
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)

        response = client.post(
            "/notifications/dismiss/",
            {"thread_uid": thread.uid, "verb": Notification.REACTION},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["deleted"], 1)
        self.assertFalse(Notification.objects.filter(recipient=self.me).exists())

    def test_dismiss_deletes_every_row_in_a_grouped_entry(self):
        """A grouped row (see test_list_groups_same_thread_same_verb) is one
        displayed entry backed by MULTIPLE Notification rows — dismissing
        it must remove all of them, not just one."""
        thread = make_thread(self.me)
        other2 = make_mask("other2")
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)
        ReactionRelation.objects.create(mask=other2, thread=thread, reaction=self.reaction)
        self.assertEqual(Notification.objects.filter(recipient=self.me).count(), 2)

        response = client.post(
            "/notifications/dismiss/",
            {"thread_uid": thread.uid, "verb": Notification.REACTION},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertEqual(response.data["deleted"], 2)
        self.assertFalse(Notification.objects.filter(recipient=self.me).exists())

    def test_dismiss_only_deletes_the_callers_own_notifications(self):
        thread = make_thread(self.me)
        someone_else = make_mask("someone_else")
        their_thread = make_thread(someone_else)
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)
        # Same verb, but belongs to a DIFFERENT recipient — must survive.
        ReactionRelation.objects.create(mask=self.other, thread=their_thread, reaction=self.reaction)

        client.post(
            "/notifications/dismiss/",
            {"thread_uid": thread.uid, "verb": Notification.REACTION},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertFalse(Notification.objects.filter(recipient=self.me).exists())
        self.assertTrue(Notification.objects.filter(recipient=someone_else).exists())

    def test_dismiss_nonexistent_group_is_a_harmless_no_op(self):
        response = client.post(
            "/notifications/dismiss/",
            {"thread_uid": "doesnotexist", "verb": Notification.REACTION},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted"], 0)

    def test_dismiss_rejects_an_invalid_verb(self):
        response = client.post(
            "/notifications/dismiss/",
            {"thread_uid": "abc123456789", "verb": "not_a_verb"},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_serializes_profile_view_with_null_thread(self):
        """profile_view rows carry no thread — Notification.thread is null
        for that one verb (see app/models/notification.py); the FE routes
        to the actor's profile instead. Locks in `allow_null=True` on the
        serializer's nested `thread` field."""
        Notification.objects.create(recipient=self.me, actor=self.other, verb=Notification.PROFILE_VIEW)

        response = client.get("/notifications/", HTTP_X_DYNAMIC_TOKEN=_token())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["thread"])
        self.assertEqual(results[0]["verb"], Notification.PROFILE_VIEW)

    def test_dismiss_profile_view_notification(self):
        Notification.objects.create(recipient=self.me, actor=self.other, verb=Notification.PROFILE_VIEW)

        response = client.post(
            "/notifications/dismiss/",
            {"verb": Notification.PROFILE_VIEW},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted"], 1)
        self.assertFalse(Notification.objects.filter(recipient=self.me).exists())

    def test_dismiss_profile_view_never_touches_thread_linked_rows(self):
        """A thread-linked notification sharing no thread must survive a
        profile_view dismiss — thread__isnull=True must scope this exactly,
        never fall back to deleting everything with that verb."""
        thread = make_thread(self.me)
        Notification.objects.create(recipient=self.me, actor=self.other, verb=Notification.PROFILE_VIEW)
        Notification.objects.create(recipient=self.me, actor=self.other, verb=Notification.SHARE, thread=thread)

        client.post(
            "/notifications/dismiss/",
            {"verb": Notification.PROFILE_VIEW},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )

        self.assertFalse(Notification.objects.filter(verb=Notification.PROFILE_VIEW).exists())
        self.assertTrue(Notification.objects.filter(verb=Notification.SHARE).exists())

    def test_dismiss_rejects_a_thread_uid_for_profile_view(self):
        response = client.post(
            "/notifications/dismiss/",
            {"thread_uid": "abc123456789", "verb": Notification.PROFILE_VIEW},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dismiss_rejects_a_missing_thread_uid_for_a_thread_verb(self):
        response = client.post(
            "/notifications/dismiss/",
            {"verb": Notification.REACTION},
            HTTP_X_DYNAMIC_TOKEN=_token(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_groups_same_thread_same_verb(self):
        """Two different reactors on the same thread collapse into one
        grouped entry with actor_count == 2 (see
        app/methods/notifications.py::group_notifications)."""
        thread = make_thread(self.me)
        other2 = make_mask("other2")
        ReactionRelation.objects.create(mask=self.other, thread=thread, reaction=self.reaction)
        ReactionRelation.objects.create(mask=other2, thread=thread, reaction=self.reaction)

        response = client.get("/notifications/", HTTP_X_DYNAMIC_TOKEN=_token())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["actor_count"], 2)
        self.assertEqual(results[0]["verb"], Notification.REACTION)
        self.assertEqual(results[0]["thread"]["uid"], thread.uid)
