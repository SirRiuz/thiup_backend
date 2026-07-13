# Python
import hashlib

# Django
from django.test import Client, TestCase, override_settings

# Models
from app.models.mask import Mask
from app.models.thread import Thread

# Libs
from app.permissions.throttling import (
    TrustedIPAnonRateThrottle,
    TrustedIPScopedRateThrottle,
)
from app.rest.serializers.thread_serializer import ThreadSerializer
from app.rest.thread_files import ThreadFilesViewSet

client = Client()


@override_settings(TRUST_CLOUDFLARE=False, TRUSTED_PROXY_COUNT=1)
class ThreadMassAssignmentTest(TestCase):
    """A client must NOT be able to set server-controlled fields via the
    create payload (momentum_score ranking, mask authorship)."""

    def setUp(self):
        self.author = Mask.objects.create(hash="a" * 64, country_code="CO")

    def _create(self, extra):
        data = {"text": "hola", "content": {"blocks": [], "entityMap": {}}, **extra}
        s = ThreadSerializer(data=data, context={"mask": self.author})
        assert s.is_valid(), s.errors
        obj = s.save(language="es", region="", geohash=None)
        obj.refresh_from_db()
        return obj

    def test_momentum_score_injection_is_ignored(self):
        obj = self._create({"momentum_score": 999999999.0})
        self.assertEqual(obj.momentum_score, 0.0)

    def test_mask_injection_cannot_override_author(self):
        victim = Mask.objects.create(hash="b" * 64, country_code="CO")
        obj = self._create({"mask": str(victim.id)})
        self.assertEqual(obj.mask_id, self.author.id)

    def test_mask_injection_cannot_null_author(self):
        obj = self._create({"mask": None})
        self.assertEqual(obj.mask_id, self.author.id)

    def test_momentum_still_readable_in_output(self):
        # Read-only, not removed: the FE still shows it under DEBUG.
        t = Thread.objects.create(mask=self.author, text="x", content={}, momentum_score=5.0)
        rep = ThreadSerializer(t, context={"mask": self.author}).data
        self.assertEqual(rep["momentum_score"], 5.0)


class TrustedIpThrottleTest(TestCase):
    def test_throttles_key_on_trusted_ip(self):
        from django.test import RequestFactory

        rf = RequestFactory()
        with override_settings(TRUST_CLOUDFLARE=False, TRUSTED_PROXY_COUNT=1):
            req = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4, 9.9.9.9")
            self.assertEqual(TrustedIPScopedRateThrottle().get_ident(req), "9.9.9.9")
            self.assertEqual(TrustedIPAnonRateThrottle().get_ident(req), "9.9.9.9")


@override_settings(TRUST_CLOUDFLARE=False, TRUSTED_PROXY_COUNT=1)
class XffSpoofingTest(TestCase):
    """A forged first X-Forwarded-For entry must NOT set request.mask."""

    def test_mask_derives_from_trusted_hop_not_forged_entry(self):
        with override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False):
            r = client.get("/me/", HTTP_X_FORWARDED_FOR="6.6.6.6, 5.5.5.5")
        self.assertEqual(r.status_code, 200)
        forged = hashlib.sha256(b"6.6.6.6").hexdigest()
        trusted = hashlib.sha256(b"5.5.5.5").hexdigest()
        self.assertEqual(r.data["mask_id"], trusted)
        self.assertNotEqual(r.data["mask_id"], forged)

    @override_settings(TRUST_CLOUDFLARE=True)
    def test_cloudflare_header_is_authoritative(self):
        with override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False):
            r = client.get(
                "/me/",
                HTTP_CF_CONNECTING_IP="4.4.4.4",
                HTTP_X_FORWARDED_FOR="6.6.6.6, 5.5.5.5",
            )
        self.assertEqual(r.data["mask_id"], hashlib.sha256(b"4.4.4.4").hexdigest())


class UploadThrottleTest(TestCase):
    def test_presign_and_confirm_are_throttled(self):
        view = ThreadFilesViewSet()
        view.action = "presign"
        throttles = view.get_throttles()
        self.assertEqual(len(throttles), 1)
        self.assertIsInstance(throttles[0], TrustedIPScopedRateThrottle)
        self.assertEqual(view.throttle_scope, "thread_files")


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ReplyToDeadThreadTest(TestCase):
    def setUp(self):
        self.author = Mask.objects.create(hash="c" * 64, country_code="CO")

    def _post_reply(self, parent_uid):
        return client.post(
            "/threads/",
            {"text": "reply", "content": {"blocks": [], "entityMap": {}}, "sub": parent_uid},
            content_type="application/json",
        )

    def test_reply_to_inactive_thread_rejected(self):
        dead = Thread.objects.create(mask=self.author, text="dead", content={}, is_active=False)
        self.assertEqual(self._post_reply(dead.uid).status_code, 404)

    def test_reply_to_hidden_thread_rejected(self):
        hidden = Thread.objects.create(mask=self.author, text="hidden", content={}, visibility=False)
        self.assertEqual(self._post_reply(hidden.uid).status_code, 404)

    def test_reply_to_live_thread_ok(self):
        live = Thread.objects.create(mask=self.author, text="live", content={})
        r = self._post_reply(live.uid)
        self.assertEqual(r.status_code, 201)
