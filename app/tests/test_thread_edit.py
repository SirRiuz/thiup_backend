# Python
import hashlib
import json
from unittest import mock

# Django
from django.core.cache import cache
from django.test import Client, TransactionTestCase, override_settings

# Models
from app.models.mask import Mask
from app.models.media import ThreadFile
from app.models.thread import Thread
from app.models.thread_edit import ThreadEditHistory
from app.permissions.throttling import TrustedIPScopedRateThrottle

client = Client()

# Mask the MaskMiddleware derives for the test client: SHA-256 of REMOTE_ADDR
# (Django's test client defaults to 127.0.0.1) — same convention as
# test_thread_files.py.
REQUESTER_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()

# The storage cleanup signal resolves the adapter lazily; patch there (same
# target as ThreadFileStorageCleanupTest in test_thread_files.py).
SIGNAL_GET_BACKEND = "app.methods.storage_backends.get_backend"


class FakeBackend:
    """In-memory storage adapter for tests (no R2, no disk)."""

    def __init__(self):
        self.deleted = []

    def delete_object(self, key):
        self.deleted.append(key)
        return True

    def delete_objects(self, keys):
        self.deleted.extend(keys)
        return len(keys)


def edit(uid, body, **extra):
    return client.post(f"/threads/{uid}/edit/", data=json.dumps(body), content_type="application/json", **extra)


def edit_history(uid, **extra):
    return client.get(f"/threads/{uid}/edit-history/", **extra)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadEditTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # The throttle counts per IP in the LocMem cache, which outlives each
        # test in the pytest process — clear it so a previous test's counter
        # never bleeds a 429 into this one (same precaution as test_thread_view.py).
        cache.clear()
        self.mask = Mask.objects.create(hash=REQUESTER_HASH, country_code="CO")
        self.thread = Thread.objects.create(content={}, text="original text", mask=self.mask)

    def _edit_body(self, **overrides):
        body = {"text": "edited text", "content": {}}
        body.update(overrides)
        return body

    def test_edit_own_thread_updates_text_and_sets_edited_at(self):
        response = edit(self.thread.uid, self._edit_body(text="new text"))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["text"], "new text")
        self.assertTrue(body["is_edited"])
        self.assertIsNotNone(body["edited_at_iso"])

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.text, "new text")
        self.assertIsNotNone(self.thread.edited_at)

    def test_edit_creates_history_row_with_previous_text(self):
        edit(self.thread.uid, self._edit_body(text="v2"))
        edit(self.thread.uid, self._edit_body(text="v3"))

        response = edit_history(self.thread.uid)
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]

        self.assertEqual(len(results), 2)
        # Newest first: the most recent edit's snapshot (what it was right
        # before becoming "v3") comes first.
        self.assertEqual(results[0]["previous_text"], "v2")
        self.assertEqual(results[1]["previous_text"], "original text")

    def test_edit_rejects_non_owner_with_404(self):
        response = edit(self.thread.uid, self._edit_body(), REMOTE_ADDR="10.0.0.2")

        self.assertEqual(response.status_code, 404)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.text, "original text")
        self.assertIsNone(self.thread.edited_at)

    def test_edit_soft_deleted_thread_404s(self):
        self.thread.is_active = False
        self.thread.save()

        response = edit(self.thread.uid, self._edit_body())
        self.assertEqual(response.status_code, 404)

    def test_edit_rejects_empty_and_whitespace_text(self):
        self.assertEqual(edit(self.thread.uid, self._edit_body(text="")).status_code, 400)
        self.assertEqual(edit(self.thread.uid, self._edit_body(text="   ")).status_code, 400)

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.text, "original text")

    @mock.patch(SIGNAL_GET_BACKEND)
    def test_edit_remove_media_deletes_thread_file_and_storage_object(self, get_backend):
        backend = FakeBackend()
        get_backend.return_value = backend
        media = ThreadFile.objects.create(
            thread=self.thread,
            mask=self.mask,
            file_key="m/aa/aa/one.webp",
            file_url="https://cdn.test/m/aa/aa/one.webp",
            is_active=True,
        )

        response = edit(self.thread.uid, self._edit_body(remove_media=[media.uid]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ThreadFile.objects.filter(uid=media.uid).exists())
        self.assertEqual(backend.deleted, ["m/aa/aa/one.webp"])

        history = ThreadEditHistory.objects.get(thread=self.thread)
        self.assertEqual(history.media_removed_count, 1)

    def test_edit_remove_media_ignores_uid_from_different_thread(self):
        other_thread = Thread.objects.create(content={}, text="other", mask=self.mask)
        media = ThreadFile.objects.create(
            thread=other_thread,
            mask=self.mask,
            file_key="m/bb/bb/two.webp",
            file_url="https://cdn.test/m/bb/bb/two.webp",
            is_active=True,
        )

        response = edit(self.thread.uid, self._edit_body(remove_media=[media.uid]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ThreadFile.objects.filter(uid=media.uid, thread=other_thread).exists())

        history = ThreadEditHistory.objects.get(thread=self.thread)
        self.assertEqual(history.media_removed_count, 0)

    def test_edit_add_media_counts_only_confirmed_owned_files(self):
        ThreadFile.objects.create(
            uid="addedfile001",
            thread=self.thread,
            mask=self.mask,
            file_key="m/cc/cc/three.webp",
            file_url="https://cdn.test/m/cc/cc/three.webp",
            is_active=True,
        )

        response = edit(self.thread.uid, self._edit_body(add_media=["addedfile001", "bogus0000001"]))

        self.assertEqual(response.status_code, 200)
        history = ThreadEditHistory.objects.get(thread=self.thread)
        self.assertEqual(history.media_added_count, 1)

    def test_edit_history_endpoint_is_public(self):
        edit(self.thread.uid, self._edit_body(text="v2"))

        # A different mask (different REMOTE_ADDR → different derived hash)
        # can still read the history — it's not owner-gated.
        response = edit_history(self.thread.uid, REMOTE_ADDR="10.0.0.3")

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["previous_text"], "original text")

    def test_edit_rate_limited_after_threshold(self):
        # SimpleRateThrottle.THROTTLE_RATES is bound to api_settings.DEFAULT_
        # THROTTLE_RATES at MODULE IMPORT time (a plain assignment, not a
        # live lookup) — @override_settings(REST_FRAMEWORK=...) can't reach
        # it after startup. Patch the already-bound class attribute instead.
        patched_rates = {**TrustedIPScopedRateThrottle.THROTTLE_RATES, "threads_edit": "1/min"}
        with mock.patch.object(TrustedIPScopedRateThrottle, "THROTTLE_RATES", patched_rates):
            first = edit(self.thread.uid, self._edit_body(text="v2"))
            second = edit(self.thread.uid, self._edit_body(text="v3"))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second.headers)

    def test_edit_reply_uses_same_endpoint(self):
        reply = Thread.objects.create(content={}, text="reply v1", sub=self.thread, mask=self.mask)

        response = edit(reply.uid, self._edit_body(text="reply v2"))

        self.assertEqual(response.status_code, 200)
        reply.refresh_from_db()
        self.assertEqual(reply.text, "reply v2")
        self.assertIsNotNone(reply.edited_at)
