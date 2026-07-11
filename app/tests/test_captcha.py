# Python
import json
import hashlib
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch, MagicMock

# Django
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework import status

# Libs
import requests
from app.methods.tokens import encode_token, issue_ticket
from app.methods.captcha import (
    verify_captcha_token,
    issue_human_pass,
    validate_human_pass,
    CaptchaUnavailable,
    PASS_PURPOSE,
)

# Models
from app.models.mask import Mask
from app.models.thread import Thread

# Gateway helper of the existing suite (transport flags are ON under pytest).
from app.tests.test_gateway import gateway_post, token as client_ticket
from app.tests.test_foryou import decode_body


# The Django test client hits the API from 127.0.0.1, so MaskMiddleware
# derives THIS mask for every request — passes must be minted for it.
CLIENT_MASK_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()

CAP_TEST_SETTINGS = {
    "CAPTCHA_PROTECT": True,
    "CAP_SITEVERIFY_URL": "http://cap.test",
    "CAP_PUBLIC_URL": "http://cap.test",
    "CAP_SITE_KEY": "site-key",
    "CAP_SECRET": "top-secret",
}


def client_mask() -> (Mask):
    mask, _ = Mask.objects.get_or_create(hash=CLIENT_MASK_HASH)
    return mask


def valid_pass() -> (str):
    return issue_human_pass(client_mask())


@override_settings(**CAP_TEST_SETTINGS)
class VerifyCaptchaTokenTest(TestCase):
    """Unit tests of the siteverify HTTP seam (network fully mocked)."""

    def _response(self, status_code=200, payload=None):
        response = MagicMock()
        response.status_code = status_code
        if payload is None:
            response.json.side_effect = ValueError("not json")
        else:
            response.json.return_value = payload
        return response

    @patch("app.methods.captcha._session.post")
    def test_success_true_calls_cap_with_exact_contract(self, post):
        post.return_value = self._response(payload={"success": True})
        self.assertTrue(verify_captcha_token("cap-token"))
        post.assert_called_once_with(
            "http://cap.test/site-key/siteverify",
            json={"secret": "top-secret", "response": "cap-token"},
            timeout=3,
        )

    @patch("app.methods.captcha._session.post")
    def test_success_false_is_denied(self, post):
        post.return_value = self._response(payload={"success": False})
        self.assertFalse(verify_captcha_token("cap-token"))

    @patch("app.methods.captcha._session.post")
    def test_non_json_body_is_denied(self, post):
        post.return_value = self._response(payload=None)
        self.assertFalse(verify_captcha_token("cap-token"))

    @patch("app.methods.captcha._session.post")
    def test_4xx_is_denied_not_unavailable(self, post):
        post.return_value = self._response(
            status_code=400, payload={"success": False})
        self.assertFalse(verify_captcha_token("cap-token"))

    @patch("app.methods.captcha._session.post")
    def test_empty_token_short_circuits_without_http(self, post):
        self.assertFalse(verify_captcha_token(""))
        post.assert_not_called()

    @patch("app.methods.captcha._session.post")
    def test_network_error_raises_unavailable(self, post):
        post.side_effect = requests.ConnectionError()
        with self.assertRaises(CaptchaUnavailable):
            verify_captcha_token("cap-token")

    @patch("app.methods.captcha._session.post")
    def test_timeout_raises_unavailable(self, post):
        post.side_effect = requests.Timeout()
        with self.assertRaises(CaptchaUnavailable):
            verify_captcha_token("cap-token")

    @patch("app.methods.captcha._session.post")
    def test_5xx_raises_unavailable(self, post):
        post.return_value = self._response(
            status_code=500, payload={"success": False})
        with self.assertRaises(CaptchaUnavailable):
            verify_captcha_token("cap-token")

    @patch("app.methods.captcha._session.post")
    def test_logs_never_contain_token_or_secret(self, post):
        post.side_effect = requests.ConnectionError()
        with self.assertLogs("app.methods.captcha", level="WARNING") as logs:
            with self.assertRaises(CaptchaUnavailable):
                verify_captcha_token("cap-token-sensitive")
        joined = "\n".join(logs.output)
        self.assertNotIn("cap-token-sensitive", joined)
        self.assertNotIn("top-secret", joined)


class HumanPassTest(TestCase):
    """Unit tests of the mask-bound human pass JWT."""

    def setUp(self):
        self.mask = client_mask()

    def test_issued_pass_validates_for_its_mask(self):
        self.assertTrue(
            validate_human_pass(issue_human_pass(self.mask), self.mask))

    def test_pass_is_rejected_for_another_mask(self):
        other = Mask.objects.create(hash="other-mask", country_code="CO")
        self.assertFalse(
            validate_human_pass(issue_human_pass(other), self.mask))

    def test_ticket_jwt_is_not_a_pass(self):
        # Signed with the same key but minted for another purpose: the
        # /ticket/ client-assertion must never open the human gate.
        self.assertFalse(validate_human_pass(issue_ticket(), self.mask))

    def test_expired_pass_is_rejected(self):
        now = datetime.now(dt_timezone.utc)
        expired = encode_token({
            "exp": now - timedelta(seconds=10),
            "purpose": PASS_PURPOSE,
            "mask": self.mask.hash,
        })
        self.assertFalse(validate_human_pass(expired, self.mask))

    def test_garbage_is_rejected(self):
        self.assertFalse(validate_human_pass("not-a-jwt", self.mask))
        self.assertFalse(validate_human_pass("", self.mask))


@override_settings(
    ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False,
    **CAP_TEST_SETTINGS)
class CaptchaVerifyViewTest(TestCase):
    """POST /captcha/verify/ exchanges a Cap token for the human pass."""

    def setUp(self):
        self.client = Client()
        cache.clear()  # isolate throttle history between tests

    def _post(self, body):
        return self.client.post(
            "/captcha/verify/", data=json.dumps(body),
            content_type="application/json")

    @patch("app.rest.captcha.verify_captcha_token", return_value=True)
    def test_valid_cap_token_mints_a_working_pass(self, verify):
        res = self._post({"token": "cap-token"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        body = decode_body(res)
        self.assertEqual(body["expires_in"], 600)
        self.assertTrue(validate_human_pass(body["pass"], client_mask()))
        verify.assert_called_once_with("cap-token")

    @patch("app.rest.captcha.verify_captcha_token", return_value=False)
    def test_invalid_cap_token_is_403(self, verify):
        res = self._post({"token": "bad"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(decode_body(res)["code"], "CAPTCHA_FAILED")

    @patch("app.rest.captcha.verify_captcha_token",
           side_effect=CaptchaUnavailable())
    def test_cap_down_is_503(self, verify):
        res = self._post({"token": "cap-token"})
        self.assertEqual(
            res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(decode_body(res)["code"], "CAPTCHA_UNAVAILABLE")

    @patch("app.rest.captcha.verify_captcha_token")
    def test_flag_off_is_404_and_never_calls_cap(self, verify):
        with override_settings(CAPTCHA_PROTECT=False):
            res = self._post({"token": "cap-token"})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(decode_body(res)["code"], "CAPTCHA_DISABLED")
        verify.assert_not_called()


def make_thread() -> (Thread):
    author = Mask.objects.create(hash="captcha-author", country_code="CO")
    return Thread.objects.create(content={}, text="protected", mask=author)


@override_settings(
    ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False,
    **CAP_TEST_SETTINGS)
class HumanValidatorScopeTest(TestCase):
    """@human_validator gates exactly the entity-creating writes."""

    PROTECTED_WRITES = (
        "/threads/",
        "/reactions/",
        "/reports/",
        "/thread-files/presign/",
        "/thread-files/confirm/",
    )

    def setUp(self):
        self.client = Client()
        cache.clear()

    def _post(self, path, body=None, human_pass=None):
        extra = {}
        if human_pass is not None:
            extra["HTTP_X_HUMAN_PASS"] = human_pass
        return self.client.post(
            path, data=json.dumps(body or {}),
            content_type="application/json", **extra)

    def test_every_protected_write_requires_a_pass(self):
        for path in self.PROTECTED_WRITES:
            res = self._post(path)
            self.assertEqual(
                res.status_code, status.HTTP_403_FORBIDDEN, path)
            self.assertEqual(decode_body(res)["code"], "CAPTCHA_FAILED", path)

    def test_valid_pass_opens_every_protected_write(self):
        # Non-403 is the assertion: an empty body may 400 downstream in the
        # serializer, which proves the gate ran and let the request through.
        human_pass = valid_pass()
        for path in self.PROTECTED_WRITES:
            res = self._post(path, human_pass=human_pass)
            self.assertNotEqual(
                res.status_code, status.HTTP_403_FORBIDDEN, path)

    def test_full_happy_path_creates_the_entity(self):
        thread = make_thread()
        res = self._post(
            "/reports/",
            body={"thread_id": thread.uid, "category": "spam_or_deception"},
            human_pass=valid_pass())
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_expired_pass_is_403(self):
        expired = encode_token({
            "exp": datetime.now(dt_timezone.utc) - timedelta(seconds=10),
            "purpose": PASS_PURPOSE,
            "mask": CLIENT_MASK_HASH,
        })
        res = self._post("/reports/", human_pass=expired)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(decode_body(res)["code"], "CAPTCHA_FAILED")

    def test_foreign_pass_is_403(self):
        other = Mask.objects.create(hash="stolen-from", country_code="CO")
        res = self._post("/reports/", human_pass=issue_human_pass(other))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_error_body_never_echoes_the_pass(self):
        human_pass = issue_human_pass(
            Mask.objects.create(hash="not-mine", country_code="CO"))
        res = self._post("/reports/", human_pass=human_pass)
        self.assertNotIn(human_pass, res.content.decode())

    def test_readonly_post_feeds_need_no_pass(self):
        for path in ("/threads/foryou/", "/threads/closeyou/"):
            res = self._post(path)
            self.assertNotEqual(
                res.status_code, status.HTTP_403_FORBIDDEN, path)

    def test_reads_need_no_pass(self):
        for path in ("/threads/", "/reactions/"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, status.HTTP_200_OK, path)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class CaptchaFlagOffTest(TestCase):
    """CAPTCHA_PROTECT=False (the default) leaves the API exactly as before."""

    def setUp(self):
        self.client = Client()
        cache.clear()

    def test_creates_work_without_any_pass(self):
        thread = make_thread()
        res = self.client.post(
            "/reports/",
            data=json.dumps({
                "thread_id": thread.uid, "category": "spam_or_deception"}),
            content_type="application/json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_verify_endpoint_reports_disabled(self):
        res = self.client.post(
            "/captcha/verify/", data=json.dumps({"token": "x"}),
            content_type="application/json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(decode_body(res)["code"], "CAPTCHA_DISABLED")


@override_settings(**CAP_TEST_SETTINGS)
class CaptchaGatewayTest(TestCase):
    """The pass header rides OUTSIDE the encrypted envelope, like the ticket,
    and must reach the inner protected view through the gateway dispatch."""

    def setUp(self):
        cache.clear()
        self.thread = make_thread()

    def test_pass_travels_through_the_gateway(self):
        body = json.dumps({
            "thread_id": self.thread.uid, "category": "spam_or_deception"})
        res = gateway_post(
            "POST", "/reports/", client_ticket(), body=body,
            extra={"HTTP_X_HUMAN_PASS": valid_pass()})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_gateway_write_without_pass_is_403(self):
        body = json.dumps({
            "thread_id": self.thread.uid, "category": "spam_or_deception"})
        res = gateway_post("POST", "/reports/", client_ticket(), body=body)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(decode_body(res)["code"], "CAPTCHA_FAILED")


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class CreateThrottleTest(TestCase):
    """Create-only rate limits: writes are capped, reads never are."""

    def setUp(self):
        self.client = Client()
        cache.clear()

    def tearDown(self):
        # This class deliberately exhausts the create rate for the shared
        # test-client IP — leave a clean window for whatever runs next.
        cache.clear()

    def test_thread_creation_hits_the_scoped_limit(self):
        # threads_create defaults to 10/min; the 11th create must be 429.
        # Bodies are invalid on purpose (400): DRF throttles BEFORE the
        # serializer runs, so the scope counts these all the same.
        statuses = []
        for _ in range(11):
            res = self.client.post(
                "/threads/", data=json.dumps({}),
                content_type="application/json")
            statuses.append(res.status_code)
        self.assertNotIn(
            status.HTTP_429_TOO_MANY_REQUESTS, statuses[:10])
        self.assertEqual(
            statuses[10], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_reads_are_not_throttled_by_the_create_scope(self):
        for _ in range(11):
            self.client.post(
                "/threads/", data=json.dumps({}),
                content_type="application/json")
        res = self.client.get("/threads/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ConfigCaptchaTest(TestCase):
    """/config/ additions are additive — the legacy contract is untouched."""

    def setUp(self):
        self.client = Client()

    def test_flag_off_exposes_disabled_state(self):
        body = decode_body(self.client.get("/config/"))
        self.assertIn("encrypted_response", body)
        self.assertIn("single_request_protect", body)
        self.assertFalse(body["captcha_protect"])
        self.assertIsNone(body["captcha_endpoint"])

    @override_settings(**CAP_TEST_SETTINGS)
    def test_flag_on_exposes_widget_endpoint_but_never_the_secret(self):
        body = decode_body(self.client.get("/config/"))
        self.assertTrue(body["captcha_protect"])
        self.assertEqual(body["captcha_endpoint"], "http://cap.test/site-key/")
        self.assertNotIn("top-secret", json.dumps(body))
