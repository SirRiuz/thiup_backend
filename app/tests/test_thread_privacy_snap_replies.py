# Python
import hashlib
import json

# Django
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

# Models
from app.models.mask import Mask
from app.models.thread import Thread

client = Client()

# Mask the MaskMiddleware derives for the test client: SHA-256 of REMOTE_ADDR
# (Django's test client defaults to 127.0.0.1) — same convention as
# test_thread_edit.py/test_thread_files.py.
REQUESTER_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()


def create_thread(**overrides):
    body = {"text": "hello world", "content": {}}
    body.update(overrides)
    return client.post("/threads/", data=json.dumps(body), content_type="application/json")


def edit_thread(uid, **overrides):
    body = {"text": "hello world edited", "content": {}}
    body.update(overrides)
    return client.post(f"/threads/{uid}/edit/", data=json.dumps(body), content_type="application/json")


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadPrivacySnapRepliesTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # Throttles/masks are cached in LocMem, which outlives each test in
        # the pytest process — clear it so a previous test's counter/mask
        # never bleeds into this one (same precaution as the other
        # thread test files).
        cache.clear()
        self.mask = Mask.objects.create(hash=REQUESTER_HASH, country_code="CO")

    # ── Private threads ──────────────────────────────────────────────

    def test_private_thread_created_flag_and_retrieve_still_works(self):
        response = create_thread(text="privacytestunique123", is_private=True)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["is_private"])

        # Direct-link retrieve must NOT be gated by is_private — that's the
        # whole point of "private", unlike visibility=False.
        retrieve = client.get(f"/threads/{body['uid']}/")
        self.assertEqual(retrieve.status_code, 200)
        self.assertTrue(retrieve.json()["is_private"])

    def test_private_thread_excluded_from_list(self):
        create_thread(text="privacylistunique456", is_private=True)
        public = create_thread(text="publiclistunique456")
        self.assertEqual(public.status_code, 201)

        listing = client.get("/threads/")
        self.assertEqual(listing.status_code, 200)
        texts = [r["text"] for r in listing.json()["results"]]
        self.assertNotIn("privacylistunique456", texts)
        self.assertIn("publiclistunique456", texts)

    def test_private_thread_excluded_from_search(self):
        create_thread(text="privacysearchunique789", is_private=True)

        results = client.get("/search/?q=privacysearchunique789")
        self.assertEqual(results.status_code, 200)
        texts = [r.get("text") for r in results.json().get("results", [])]
        self.assertNotIn("privacysearchunique789", texts)

    def test_private_thread_editable_back_to_public(self):
        created = create_thread(text="toggleprivacyunique", is_private=True)
        uid = created.json()["uid"]

        toggled = edit_thread(uid, text="toggleprivacyunique", is_private=False)
        self.assertEqual(toggled.status_code, 200)
        self.assertFalse(toggled.json()["is_private"])

        listing = client.get("/threads/")
        texts = [r["text"] for r in listing.json()["results"]]
        self.assertIn("toggleprivacyunique", texts)

    # ── Snap threads ─────────────────────────────────────────────────

    def test_snap_thread_sets_expiry_roughly_24h_out(self):
        response = create_thread(is_snap=True)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["is_snap"])
        self.assertIsNotNone(body["expires_at_iso"])

        thread = Thread.objects.get(uid=body["uid"])
        delta = thread.expire_date - timezone.now()
        self.assertGreater(delta.total_seconds(), 23 * 3600)
        self.assertLess(delta.total_seconds(), 25 * 3600)

    def test_snap_flag_is_immutable_via_edit(self):
        created = create_thread(is_snap=True)
        uid = created.json()["uid"]
        thread = Thread.objects.get(uid=uid)
        original_expire = thread.expire_date

        # ThreadEditSerializer has no is_snap/expire_date field at all — any
        # such keys in the body are simply ignored (not a validation error).
        response = edit_thread(uid, is_snap=False, expire_date=None)
        self.assertEqual(response.status_code, 200)

        thread.refresh_from_db()
        self.assertTrue(thread.is_snap)
        self.assertEqual(thread.expire_date, original_expire)

    def test_purge_inactive_deactivates_expired_snap_thread(self):
        created = create_thread(is_snap=True)
        uid = created.json()["uid"]
        thread = Thread.objects.get(uid=uid)
        # Force it into the past directly — no need to wait 24h in a test.
        thread.expire_date = timezone.now() - timezone.timedelta(hours=1)
        thread.save(update_fields=["expire_date"])

        call_command("purge_inactive")

        thread.refresh_from_db()
        self.assertFalse(thread.is_active)

    # ── Disable replies ──────────────────────────────────────────────

    def test_reply_rejected_when_parent_disabled_replies(self):
        parent = create_thread(text="repliesoffparent", replies_disabled=True)
        parent_uid = parent.json()["uid"]

        reply = create_thread(text="a reply", sub=parent_uid)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(reply.json().get("detail"), "replies_disabled")

    def test_reply_allowed_after_re_enabling(self):
        parent = create_thread(text="repliesreenableparent", replies_disabled=True)
        parent_uid = parent.json()["uid"]

        edit_thread(parent_uid, text="repliesreenableparent", replies_disabled=False)

        reply = create_thread(text="now allowed", sub=parent_uid)
        self.assertEqual(reply.status_code, 201)

    def test_replies_disabled_exposed_and_editable(self):
        created = create_thread(text="repliesflagparent")
        uid = created.json()["uid"]
        self.assertFalse(created.json()["replies_disabled"])

        toggled = edit_thread(uid, text="repliesflagparent", replies_disabled=True)
        self.assertTrue(toggled.json()["replies_disabled"])
