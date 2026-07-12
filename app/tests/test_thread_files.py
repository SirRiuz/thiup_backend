# Python
import base64
import hashlib
import json
from unittest import mock

# Libs
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# Django
from django.test import Client, TestCase, override_settings

# Models
from app.models.mask import Mask
from app.models.media import ThreadFile
from app.models.thread import Thread

client = Client()

PRESIGN_URL = "/thread-files/presign/"
CONFIRM_URL = "/thread-files/confirm/"


def decode_body(response):
    """Read the response body, encrypted (application/raw) or plain. Mirrors the
    frontend decrypt; the response renderer encrypts whenever it's active, so
    this is needed even under @override_settings(ENCRYPTED_RESPONSE=False)."""
    if "application/raw" not in (response.headers.get("Content-Type") or ""):
        return response.json()
    payload = response.headers["X-Response-Payload"][::-1]
    key_b64, iv_b64 = base64.b64decode(payload).decode().split(":")
    cipher = AES.new(base64.b64decode(key_b64), AES.MODE_CBC, base64.b64decode(iv_b64))
    raw = base64.b64decode(response.content.decode()[::-1])
    return json.loads(unpad(cipher.decrypt(raw), AES.block_size).decode())


# Mask the MaskMiddleware derives for the test client: SHA-256 of REMOTE_ADDR
# (Django's test client defaults to 127.0.0.1).
REQUESTER_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()

# The view resolves the storage adapter via get_backend(); patch it there.
GET_BACKEND = "app.rest.thread_files.get_backend"


def post(url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


class FakeBackend:
    """In-memory storage adapter for tests (no R2, no disk)."""

    def __init__(self, exists=True, content_length=81000):
        self._exists = exists
        self._content_length = content_length
        self.deleted = []
        self.batch_calls = 0

    def generate_upload(self, key, content_type, expires=None, base_url=None):
        return {
            "upload_url": f"https://fake.example/{key}",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_in": 600,
        }

    def object_exists(self, key):
        return {"content_length": self._content_length, "content_type": "image/webp"} if self._exists else None

    def public_url(self, key, base_url=None):
        return f"https://cdn.test/{key}"

    def delete_object(self, key):
        self.deleted.append(key)
        return True

    def delete_objects(self, keys):
        self.batch_calls += 1
        self.deleted.extend(keys)
        return len(keys)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadFilePresignTest(TestCase):
    def setUp(self):
        self.mask = Mask.objects.create(hash=REQUESTER_HASH, country_code="CO")

    @mock.patch(GET_BACKEND, return_value=FakeBackend())
    def test_presign_returns_contract_and_creates_detached(self, _backend):
        response = post(
            PRESIGN_URL,
            {
                "content_type": "image/webp",
                "is_video": False,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = decode_body(response)
        self.assertEqual(body["method"], "PUT")
        self.assertEqual(body["headers"]["Content-Type"], "image/webp")
        self.assertEqual(body["upload_url"], f"https://fake.example/{body['key']}")
        self.assertEqual(body["public_url"], f"https://cdn.test/{body['key']}")

        # Sharded key: 'm/<c1c2>/<c3c4>/<token>.webp'.
        parts = body["key"].split("/")
        self.assertEqual(parts[0], "m")
        self.assertEqual(len(parts), 4)
        self.assertEqual(len(parts[1]), 2)
        self.assertEqual(len(parts[2]), 2)
        self.assertTrue(parts[-1].endswith(".webp"))

        # The object name is a long random token; the shards are its first
        # chars; decoupled from uid/id.
        token = parts[-1][: -len(".webp")]
        self.assertGreaterEqual(len(token), 40)
        self.assertTrue(token.startswith(parts[1] + parts[2]))
        self.assertNotIn(body["uid"], body["key"])

        # A DETACHED (no thread), mask-owned, inactive record now exists, with
        # the key stored as a plain reference (file_key).
        pending = ThreadFile.objects.get(uid=body["uid"])
        self.assertFalse(pending.is_active)
        self.assertIsNone(pending.thread_id)
        self.assertEqual(pending.mask_id, self.mask.id)
        self.assertEqual(pending.file_key, body["key"])
        # URL is persisted ALREADY at presign (so unconfirmed uploads aren't
        # orphans without a URL).
        self.assertEqual(pending.file_url, body["public_url"])

    @mock.patch(GET_BACKEND, return_value=FakeBackend())
    def test_presign_accepts_mov_video(self, _backend):
        response = post(
            PRESIGN_URL,
            {
                "content_type": "video/quicktime",
                "is_video": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        body = decode_body(response)
        self.assertTrue(body["key"].endswith(".mov"))
        self.assertTrue(ThreadFile.objects.get(uid=body["uid"]).is_video)

    def test_presign_rejects_bad_content_type(self):
        response = post(
            PRESIGN_URL,
            {
                "content_type": "image/gif",
                "is_video": False,
            },
        )
        self.assertEqual(response.status_code, 400)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadFileConfirmTest(TestCase):
    def setUp(self):
        self.mask = Mask.objects.create(hash=REQUESTER_HASH, country_code="CO")
        self.thread = Thread.objects.create(content={}, text="hello", mask=self.mask)
        self.uid = "filabc123456"
        self.key = f"uploads/{self.uid}.webp"
        self.pending = ThreadFile.objects.create(
            uid=self.uid, file_key=self.key, mask=self.mask, thread=None, is_video=False, is_active=False
        )

    def _confirm_body(self, **overrides):
        body = {
            "uid": self.uid,
            "thread": self.thread.uid,
            "is_video": False,
            "width": 1408,
            "height": 2112,
            "target_color": "#060503",
            "is_nsfw": True,
            "metadata": {
                "archivo": "1782233359562337.jpg",
                "prediccion": "Bloquear",
                "evil": "drop-me",  # not whitelisted
            },
        }
        body.update(overrides)
        return body

    @mock.patch(GET_BACKEND, return_value=FakeBackend(exists=True))
    def test_confirm_attaches_and_stores_metadata(self, _backend):
        response = post(CONFIRM_URL, self._confirm_body())

        self.assertEqual(response.status_code, 200)
        body = decode_body(response)
        self.assertEqual(body["uid"], self.uid)
        self.assertEqual(body["key"], self.key)
        # The full public URL is returned and persisted (not just the key).
        self.assertEqual(body["file_url"], f"https://cdn.test/{self.key}")
        self.assertEqual(body["public_url"], f"https://cdn.test/{self.key}")
        self.assertTrue(body["is_nsfw"])
        # width/height/target_color live INSIDE metadata now (not columns).
        self.assertEqual(body["metadata"]["width"], 1408)
        self.assertEqual(body["metadata"]["height"], 2112)
        self.assertEqual(body["metadata"]["target_color"], "#060503")
        self.assertEqual(body["metadata"]["prediccion"], "Bloquear")
        self.assertNotIn("evil", body["metadata"])

        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_active)
        self.assertEqual(self.pending.thread_id, self.thread.id)
        self.assertEqual(self.pending.metadata["height"], 2112)
        # Full public URL persisted on the record; key kept for storage ops.
        self.assertEqual(self.pending.file_url, f"https://cdn.test/{self.key}")
        self.assertEqual(self.pending.file_key, self.key)

    @override_settings(UPLOAD_MAX_BYTES=512 * 1024 * 1024)
    @mock.patch(
        GET_BACKEND,
        return_value=FakeBackend(content_length=512 * 1024 * 1024 + 1),
    )
    def test_confirm_rejects_object_over_hard_cap(self, _backend):
        # The general 512 MB hard cap — uses the object's ACTUAL size (can't be
        # spoofed by the client). Over the cap → 413, and stays unconfirmed.
        response = post(CONFIRM_URL, self._confirm_body())

        self.assertEqual(response.status_code, 413)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)
        self.assertIsNone(self.pending.thread_id)

    @override_settings(UPLOAD_MAX_BYTES=512 * 1024 * 1024)
    @mock.patch(
        GET_BACKEND,
        return_value=FakeBackend(content_length=512 * 1024 * 1024),
    )
    def test_confirm_accepts_object_at_hard_cap(self, _backend):
        # Exactly at the cap is allowed (only strictly greater is rejected).
        response = post(CONFIRM_URL, self._confirm_body())

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_active)

    @mock.patch(GET_BACKEND, return_value=FakeBackend(exists=False))
    def test_confirm_fails_when_object_missing(self, _backend):
        response = post(CONFIRM_URL, self._confirm_body())

        self.assertEqual(response.status_code, 400)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)
        self.assertIsNone(self.pending.thread_id)

    @mock.patch(GET_BACKEND, return_value=FakeBackend(exists=True))
    def test_confirm_rejects_thread_not_owned(self, _backend):
        other_mask = Mask.objects.create(hash="hash-other", country_code="CO")
        other_thread = Thread.objects.create(content={}, text="x", mask=other_mask)

        response = post(CONFIRM_URL, self._confirm_body(thread=other_thread.uid))
        self.assertEqual(response.status_code, 404)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)

    @mock.patch(GET_BACKEND, return_value=FakeBackend(exists=True))
    def test_confirm_rejects_file_not_owned(self, _backend):
        other_mask = Mask.objects.create(hash="hash-other", country_code="CO")
        ThreadFile.objects.create(
            uid="otherfile123",
            file_key="uploads/otherfile123.webp",
            mask=other_mask,
            thread=None,
            is_video=False,
            is_active=False,
        )

        response = post(CONFIRM_URL, self._confirm_body(uid="otherfile123"))
        self.assertEqual(response.status_code, 404)


class ThreadMediaSerializerTests(TestCase):
    """Public media representation: width/height/target_color are surfaced FROM
    metadata (no dedicated columns) so the client can reserve the media's real
    aspect-ratio before it loads (no layout jump). The rest of the metadata
    JSON stays internal."""

    def test_exposes_dimensions_and_color_from_metadata(self):
        from app.rest.serializers.media_serializer import ThreadMediaSerializer

        record = ThreadFile.objects.create(
            file_key="m/ab/cd/token.mp4",
            file_url="https://cdn.test/m/ab/cd/token.mp4",
            is_video=True,
            metadata={"width": 1920, "height": 1080, "target_color": "#161c1e"},
        )
        data = ThreadMediaSerializer(record).data
        self.assertEqual(data["width"], 1920)
        self.assertEqual(data["height"], 1080)
        self.assertEqual(data["target_color"], "#161c1e")
        self.assertEqual(data["file"], "https://cdn.test/m/ab/cd/token.mp4")
        # Only the surfaced trio leaves the API — never the raw metadata/key.
        self.assertNotIn("metadata", data)
        self.assertNotIn("file_key", data)

    def test_missing_or_junk_dimensions_fall_back_to_zero(self):
        from app.rest.serializers.media_serializer import ThreadMediaSerializer

        record = ThreadFile.objects.create(
            file_key="m/ab/cd/token2.webp",
            file_url="https://cdn.test/m/ab/cd/token2.webp",
            metadata={"width": "junk"},
        )
        data = ThreadMediaSerializer(record).data
        self.assertEqual(data["width"], 0)
        self.assertEqual(data["height"], 0)


class ThreadFileStorageCleanupTest(TestCase):
    """Deleting a ThreadFile row must also remove its object from storage
    (post_delete signal -> StorageBackend.delete_object), for EVERY deletion
    path. Soft-delete keeps the binary."""

    # The signal resolves the adapter lazily from storage_backends; patch there.
    SIGNAL_GET_BACKEND = "app.methods.storage_backends.get_backend"

    def setUp(self):
        self.backend = FakeBackend()
        patcher = mock.patch(self.SIGNAL_GET_BACKEND, return_value=self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_file(self, key="m/ab/cd/token.webp", thread=None):
        return ThreadFile.objects.create(file_key=key, file_url=f"https://cdn.test/{key}", thread=thread)

    def test_instance_delete_removes_storage_object(self):
        record = self._make_file()
        record.delete()
        self.assertEqual(self.backend.deleted, ["m/ab/cd/token.webp"])

    def test_thread_cascade_delete_removes_storage_object(self):
        thread = Thread.objects.create(text="hola mundo", content={})
        self._make_file(key="m/xy/zz/cascade.webp", thread=thread)
        thread.delete()
        self.assertEqual(self.backend.deleted, ["m/xy/zz/cascade.webp"])

    def test_queryset_bulk_delete_removes_every_object(self):
        self._make_file(key="m/aa/aa/one.webp")
        self._make_file(key="m/bb/bb/two.webp")
        ThreadFile.objects.all().delete()
        self.assertEqual(
            sorted(self.backend.deleted),
            ["m/aa/aa/one.webp", "m/bb/bb/two.webp"],
        )

    def test_empty_file_key_never_touches_storage(self):
        record = ThreadFile.objects.create(file_key="")
        record.delete()
        self.assertEqual(self.backend.deleted, [])

    def test_storage_failure_does_not_block_the_row_delete(self):
        record = self._make_file(key="m/cc/cc/flaky.webp")
        with mock.patch.object(self.backend, "delete_object", side_effect=RuntimeError("boom")):
            record.delete()
        self.assertFalse(ThreadFile.objects.filter(pk=record.pk).exists())

    def test_soft_delete_keeps_the_storage_object(self):
        record = self._make_file(key="m/dd/dd/kept.webp")
        record.disable()
        self.assertEqual(self.backend.deleted, [])
        self.assertFalse(ThreadFile.objects.get(pk=record.pk).is_active)
