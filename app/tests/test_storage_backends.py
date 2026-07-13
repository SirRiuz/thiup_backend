# Python
import io
import os
import tempfile
from unittest import mock

# Django
from botocore.exceptions import ClientError
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings

# Libs
from app.methods import object_storage, storage_backends
from app.methods.storage_backends import (
    LOCAL_UPLOAD_ROUTE,
    LocalStorageBackend,
    R2StorageBackend,
    build_object_key,
    get_backend,
    local_backend_active,
    local_path,
    local_upload_put,
)


def _client_error(code):
    return ClientError({"Error": {"Code": code}}, "operation")


class ObjectStorageTest(TestCase):
    """Unit tests for the S3/R2 helpers with the boto3 client mocked out —
    no network, no bucket."""

    def setUp(self):
        self.client = mock.Mock()
        object_storage._client = self.client

    def tearDown(self):
        object_storage._client = None

    def test_get_client_is_cached(self):
        object_storage._client = None
        with mock.patch("app.methods.object_storage.boto3") as boto3_mock:
            boto3_mock.client.return_value = mock.sentinel.client
            first = object_storage._get_client()
            second = object_storage._get_client()
        self.assertIs(first, mock.sentinel.client)
        self.assertIs(second, mock.sentinel.client)
        boto3_mock.client.assert_called_once()

    def test_public_url_with_and_without_domain(self):
        with mock.patch("app.methods.object_storage.config", side_effect=lambda n, default="": "cdn.example.com"):
            self.assertEqual(object_storage.public_url("m/aa/x.webp"), "https://cdn.example.com/m/aa/x.webp")
        with mock.patch("app.methods.object_storage.config", side_effect=lambda n, default="": ""):
            self.assertEqual(object_storage.public_url("m/aa/x.webp"), "m/aa/x.webp")

    def test_generate_presigned_put_binds_key_and_content_type(self):
        self.client.generate_presigned_url.return_value = "https://signed.example/put"
        url = object_storage.generate_presigned_put("m/aa/x.webp", "image/webp", expires=60)
        self.assertEqual(url, "https://signed.example/put")
        _, kwargs = self.client.generate_presigned_url.call_args
        self.assertEqual(kwargs["Params"]["Key"], "m/aa/x.webp")
        self.assertEqual(kwargs["Params"]["ContentType"], "image/webp")
        self.assertEqual(kwargs["ExpiresIn"], 60)

    def test_delete_object_success_and_failure(self):
        self.assertTrue(object_storage.delete_object("m/aa/x.webp"))
        self.client.delete_object.side_effect = _client_error("AccessDenied")
        self.assertFalse(object_storage.delete_object("m/aa/x.webp"))

    def test_delete_objects_batches_and_counts_failures(self):
        # 1002 keys → two DeleteObjects calls; the second reports 1 failure.
        keys = [f"k{i}" for i in range(1002)]
        self.client.delete_objects.side_effect = [
            {"Errors": []},
            {"Errors": [{"Key": "k1001"}]},
        ]
        self.assertEqual(object_storage.delete_objects(keys), 1001)
        self.assertEqual(self.client.delete_objects.call_count, 2)

    def test_delete_objects_client_error_skips_chunk(self):
        self.client.delete_objects.side_effect = _client_error("AccessDenied")
        self.assertEqual(object_storage.delete_objects(["a", "b"]), 0)

    def test_head_object_found_missing_and_error(self):
        self.client.head_object.return_value = {"ContentLength": 10, "ContentType": "image/webp"}
        self.assertEqual(
            object_storage.head_object("k"),
            {"content_length": 10, "content_type": "image/webp"},
        )
        self.client.head_object.side_effect = _client_error("404")
        self.assertIsNone(object_storage.head_object("k"))
        # Unexpected errors fail CLOSED (treated as not-found).
        self.client.head_object.side_effect = _client_error("AccessDenied")
        self.assertIsNone(object_storage.head_object("k"))


class BuildObjectKeyTest(TestCase):
    def test_sharded_layout(self):
        key = build_object_key("RtfAnE4zTOKEN", "webp")
        self.assertEqual(key, "m/Rt/fA/RtfAnE4zTOKEN.webp")

    def test_short_token_skips_empty_shards(self):
        # A token shorter than the shard span still builds a valid key.
        key = build_object_key("ab", "png")
        self.assertEqual(key, "m/ab/ab.png")


class R2BackendTest(TestCase):
    """The R2 adapter is a thin delegation layer over object_storage."""

    def test_delegations(self):
        backend = R2StorageBackend()
        with (
            mock.patch.object(object_storage, "generate_presigned_put", return_value="https://signed") as put,
            mock.patch.object(object_storage, "head_object", return_value={"content_length": 1}) as head,
            mock.patch.object(object_storage, "public_url", return_value="https://cdn/k") as public,
            mock.patch.object(object_storage, "delete_object", return_value=True) as delete,
            mock.patch.object(object_storage, "delete_objects", return_value=2) as delete_many,
        ):
            upload = backend.generate_upload("k", "image/webp", expires=30)
            self.assertEqual(upload["upload_url"], "https://signed")
            self.assertEqual(upload["method"], "PUT")
            self.assertEqual(upload["headers"], {"Content-Type": "image/webp"})
            self.assertEqual(upload["expires_in"], 30)
            self.assertEqual(backend.object_exists("k"), {"content_length": 1})
            self.assertEqual(backend.public_url("k"), "https://cdn/k")
            self.assertTrue(backend.delete_object("k"))
            self.assertEqual(backend.delete_objects(["a", "b"]), 2)
        put.assert_called_once_with("k", "image/webp", 30)
        head.assert_called_once_with("k")
        public.assert_called_once_with("k")
        delete.assert_called_once_with("k")
        delete_many.assert_called_once_with(["a", "b"])

    @override_settings(UPLOAD_PRESIGN_EXPIRES=600)
    def test_generate_upload_defaults_expiry(self):
        backend = R2StorageBackend()
        with mock.patch.object(object_storage, "generate_presigned_put", return_value="u"):
            self.assertEqual(backend.generate_upload("k", "image/webp")["expires_in"], 600)


class LocalBackendTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = self.tmp.name

    def test_generate_upload_points_to_internal_route(self):
        backend = LocalStorageBackend()
        upload = backend.generate_upload("m/aa/x.webp", "image/webp", base_url="http://localhost:8080/")
        self.assertEqual(upload["upload_url"], f"http://localhost:8080/{LOCAL_UPLOAD_ROUTE}/m/aa/x.webp")
        self.assertEqual(upload["method"], "PUT")

    def test_object_exists_and_idempotent_delete(self):
        backend = LocalStorageBackend()
        with override_settings(MEDIA_ROOT=self.media_root):
            self.assertIsNone(backend.object_exists("m/aa/x.webp"))
            path = os.path.join(self.media_root, "m/aa/x.webp")
            os.makedirs(os.path.dirname(path))
            with open(path, "wb") as handle:
                handle.write(b"12345")
            self.assertEqual(backend.object_exists("m/aa/x.webp")["content_length"], 5)
            self.assertTrue(backend.delete_object("m/aa/x.webp"))
            self.assertFalse(os.path.exists(path))
            # Deleting a missing key mirrors R2's idempotency.
            self.assertTrue(backend.delete_object("m/aa/x.webp"))

    def test_public_url_uses_media_url(self):
        backend = LocalStorageBackend()
        url = backend.public_url("m/aa/x.webp", base_url="http://localhost:8080/")
        self.assertEqual(url, "http://localhost:8080/media/m/aa/x.webp")

    def test_batch_delete_fallback_counts_keys(self):
        backend = LocalStorageBackend()
        with override_settings(MEDIA_ROOT=self.media_root):
            self.assertEqual(backend.delete_objects(["m/a.webp", "m/b.webp"]), 2)


class LocalPathGuardTest(TestCase):
    def test_traversal_is_rejected(self):
        with override_settings(MEDIA_ROOT=tempfile.gettempdir()):
            self.assertIsNone(local_path("../../etc/passwd"))
            self.assertIsNotNone(local_path("m/aa/x.webp"))


class LocalUploadPutTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = self.tmp.name

    def _put(self, key, data=b"bytes", **extra):
        return self.factory.put(f"/{LOCAL_UPLOAD_ROUTE}/{key}", data=data, content_type="image/webp", **extra)

    def test_writes_body_to_media_root(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            response = local_upload_put(self._put("m/aa/x.webp", b"hello"), "m/aa/x.webp")
        self.assertEqual(response.status_code, 200)
        with open(os.path.join(self.media_root, "m/aa/x.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"hello")

    def test_rejects_non_put(self):
        response = local_upload_put(self.factory.get("/x"), "m/aa/x.webp")
        self.assertEqual(response.status_code, 405)

    def test_rejects_declared_oversize(self):
        with override_settings(MEDIA_ROOT=self.media_root, UPLOAD_MAX_BYTES=4):
            response = local_upload_put(self._put("m/aa/x.webp", b"12345"), "m/aa/x.webp")
        self.assertEqual(response.status_code, 413)

    def test_rejects_streamed_oversize_and_removes_partial(self):
        # The declared Content-Length lies (passes the early check) but the
        # actual stream exceeds the cap → 413 and the partial file is removed.
        # The stream is swapped by hand because Django's LimitedStream would
        # otherwise clamp reads to the declared length.
        request = self._put("m/aa/x.webp", b"", CONTENT_LENGTH="4")
        request._stream = io.BytesIO(b"0123456789abcdef")
        request._read_started = False
        with override_settings(MEDIA_ROOT=self.media_root, UPLOAD_MAX_BYTES=8):
            response = local_upload_put(request, "m/aa/x.webp")
        self.assertEqual(response.status_code, 413)
        self.assertFalse(os.path.exists(os.path.join(self.media_root, "m/aa/x.webp")))

    def test_rejects_traversal_key(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            response = local_upload_put(self._put("../../evil", b"x"), "../../evil")
        self.assertEqual(response.status_code, 400)


class BackendSelectionTest(TestCase):
    """get_backend()/local_backend_active() pick the adapter from the env."""

    def setUp(self):
        self._saved = storage_backends._backend
        storage_backends._backend = None

    def tearDown(self):
        storage_backends._backend = self._saved

    def _with_r2(self, configured):
        value = "x" if configured else ""
        return mock.patch("app.methods.storage_backends.config", side_effect=lambda n, default="": value)

    def test_r2_configured_wins(self):
        with self._with_r2(True):
            self.assertIsInstance(get_backend(), R2StorageBackend)

    def test_local_debug_plain_transport(self):
        with self._with_r2(False), override_settings(DEBUG=True, ENCRYPTED_RESPONSE=False):
            self.assertIsInstance(get_backend(), LocalStorageBackend)
            self.assertTrue(local_backend_active())

    def test_local_requires_plain_transport(self):
        with self._with_r2(False), override_settings(DEBUG=True, ENCRYPTED_RESPONSE=True):
            with self.assertRaises(ImproperlyConfigured):
                get_backend()
            self.assertFalse(local_backend_active())

    def test_prod_without_r2_fails_closed(self):
        with self._with_r2(False), override_settings(DEBUG=False, ENCRYPTED_RESPONSE=True):
            with self.assertRaises(ImproperlyConfigured):
                get_backend()

    def test_backend_is_memoized(self):
        with self._with_r2(True):
            self.assertIs(get_backend(), get_backend())
