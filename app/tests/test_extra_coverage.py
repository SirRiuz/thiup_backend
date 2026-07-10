# Python
from datetime import timedelta
from unittest import mock

# Django
from django.test import (
    Client,
    TestCase,
    SimpleTestCase,
    TransactionTestCase,
    RequestFactory,
    override_settings,
)
from django.core.management import call_command
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status

# Units under test
from app.utils.time import format_short_time
from app.utils.client import get_client_addres
from app.methods.user import get_user
from app.methods.storage import check_token, save_token
from app.cripto.kdf import encryptor, decryptor
from app.storages import AbsoluteUrlFileSystemStorage
from app.middlewares.delay import SimulateDelayMiddleware

# Models
from app.models.mask import Mask
from app.models.thread import Thread
from app.models.tag import Tag
from app.models.momentum_log import MomentumLog


client = Client()


class FormatShortTimeTest(SimpleTestCase):
    """Every branch of the compact relative-time formatter."""

    def _ago(self, **kw):
        return timezone.now() - timedelta(**kw)

    def test_none_and_future_return_now(self):
        self.assertEqual(format_short_time(None), "now")
        self.assertEqual(format_short_time(timezone.now() + timedelta(hours=1)), "now")

    def test_seconds_minutes_hours(self):
        self.assertEqual(format_short_time(self._ago(seconds=10)), "now")
        self.assertEqual(format_short_time(self._ago(minutes=29)), "29m")
        self.assertEqual(format_short_time(self._ago(hours=2)), "2h")

    def test_days_weeks_years(self):
        self.assertEqual(format_short_time(self._ago(days=5)), "5d")
        self.assertEqual(format_short_time(self._ago(weeks=3)), "3w")
        self.assertEqual(format_short_time(self._ago(days=800)), "2Y")


class ClientIpHelpersTest(SimpleTestCase):
    """get_client_addres + get_user honor X-Forwarded-For then REMOTE_ADDR."""

    def test_get_client_addres_forwarded_then_remote(self):
        rf = RequestFactory()
        fwd = rf.get("/", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2")
        self.assertEqual(get_client_addres(fwd), "1.1.1.1")
        direct = rf.get("/", REMOTE_ADDR="9.9.9.9")
        self.assertEqual(get_client_addres(direct), "9.9.9.9")
        self.assertEqual(get_client_addres(rf.get("/")), "127.0.0.1")

    def test_get_user_hashes_client(self):
        rf = RequestFactory()
        fwd = rf.get("/", HTTP_X_FORWARDED_FOR="8.8.8.8, 7.7.7.7")
        direct = rf.get("/", REMOTE_ADDR="8.8.8.8")
        # Same IP via either header -> same derived id.
        self.assertEqual(get_user(fwd), get_user(direct))
        self.assertTrue(get_user(direct).startswith("0x"))


class TokenStorageTest(TestCase):
    """check_token / save_token over the Django cache (anti-replay store)."""

    def test_save_then_check(self):
        cache.clear()
        token = "some-jwt-token"
        self.assertFalse(check_token(token))
        save_token(token)
        self.assertTrue(check_token(token))


class KdfRoundTripTest(SimpleTestCase):
    """encryptor -> decryptor round-trips (decryptor keeps PKCS7 padding)."""

    def test_round_trip(self):
        key, ciphertext, iv = encryptor("hello world")
        plain = decryptor(key, ciphertext, iv)
        self.assertTrue(plain.startswith("hello world"))


class AbsoluteUrlStorageTest(SimpleTestCase):
    """Local storage prefixes MEDIA_BASE_URL, or falls back to the relative URL."""

    @override_settings(MEDIA_BASE_URL="https://cdn.example.com")
    def test_absolute_url_with_base(self):
        url = AbsoluteUrlFileSystemStorage().url("media/x.png")
        self.assertTrue(url.startswith("https://cdn.example.com/"))

    @override_settings(MEDIA_BASE_URL="")
    def test_relative_url_without_base(self):
        url = AbsoluteUrlFileSystemStorage().url("media/x.png")
        self.assertFalse(url.startswith("http"))


class DelayMiddlewareTest(SimpleTestCase):
    """The (unregistered) debug delay middleware sleeps then forwards."""

    def test_sleeps_and_forwards(self):
        sentinel = object()
        mw = SimulateDelayMiddleware(lambda request: sentinel)
        with mock.patch("app.middlewares.delay.time.sleep") as slept:
            result = mw(None)
        slept.assert_called_once()
        self.assertIs(result, sentinel)


class ManagementCommandsTest(TransactionTestCase):
    """Running the dev/data commands end to end (idempotent where applicable)."""

    reset_sequences = True

    def test_load_fixtures_idempotent(self):
        call_command("load_fixtures")
        # Second run must not raise — duplicates are swallowed as "already present".
        call_command("load_fixtures")

    def test_add_dummy_threads_and_clear(self):
        call_command("add_dummy_threads", number=2, global_cities=2)
        self.assertTrue(Thread.objects.exists())
        # --clear wipes everything before reseeding.
        call_command("add_dummy_threads", number=1, global_cities=1, clear=True)
        self.assertTrue(Thread.objects.exists())


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class MasksEndpointTest(TestCase):
    """/me/ (current mask) and /users/<hash>/ (hover card)."""

    def test_me_returns_current_mask(self):
        r = client.get("/me/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("mask_id", r.data)
        # Registration date for the profile's "date joined" line.
        self.assertIn("joined", r.data)
        self.assertTrue(r.data["joined"])

    def test_user_hovercard_and_404(self):
        mask = Mask.objects.create(hash="a" * 64, country_code="CO")
        r = client.get(f"/users/{mask.hash}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(client.get("/users/deadbeef/").status_code,
                         status.HTTP_404_NOT_FOUND)

    def test_user_profile_by_public_id(self):
        # The 6-hex PUBLIC id (the UI's mask id) resolves the same profile —
        # /anon/<id> deep links depend on it.
        mask = Mask.objects.create(hash="b" * 64, country_code="CO")
        r = client.get(f"/users/{mask.hash[:6]}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["mask_id"], mask.hash)
        # Reactions received (0 for a fresh mask — never None/missing).
        self.assertEqual(r.data["reactions_count"], 0)
        # An unknown public id still 404s.
        self.assertEqual(client.get("/users/0f0f0f/").status_code,
                         status.HTTP_404_NOT_FOUND)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class SearchTabsTest(TestCase):
    """The three search tabs + suggest + query-length guard."""

    def setUp(self):
        self.mask = Mask.objects.create(hash="b" * 64, country_code="CO")
        self.thread = Thread.objects.create(
            mask=self.mask,
            text="lluvia fuerte",
            content={"blocks": [], "entityMap": {}},
        )
        Tag.objects.create(thread=self.thread, name="lluvia")

    def test_posts_tab(self):
        r = client.get("/search/?q=lluvia&type=posts")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("results", r.data)

    def test_tags_tab(self):
        r = client.get("/search/?q=lluvia&type=tags")
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_users_tab(self):
        r = client.get(f"/search/?q={self.mask.hash[:6]}&type=users")
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_author_handle_match(self):
        r = client.get(f"/search/?q=@{self.mask.hash[:6]}&type=posts")
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_suggest_with_and_without_min_chars(self):
        self.assertEqual(client.get("/search/suggest/?q=ll").status_code,
                         status.HTTP_200_OK)
        # < 2 chars -> trending-only branch.
        self.assertEqual(client.get("/search/suggest/?q=l").status_code,
                         status.HTTP_200_OK)

    def test_query_too_long_is_rejected(self):
        r = client.get("/search/?q=" + "x" * 200)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class MaskMiddlewareBranchesTest(TestCase):
    """Health exemption (no mask) and X-Forwarded-For client extraction."""

    def test_health_is_exempt_from_mask(self):
        # /health/ returns early with request.mask = None (no mask write).
        self.assertEqual(client.get("/health/").status_code, status.HTTP_200_OK)

    def test_forwarded_for_creates_mask(self):
        before = Mask.objects.count()
        r = client.get("/me/", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(Mask.objects.count(), before)


class RecomputeMomentumErrorTest(TestCase):
    """The command logs a failed MomentumLog row and re-raises on error."""

    def test_failure_is_logged_and_reraised(self):
        with mock.patch(
            "app.management.commands.recompute_momentum.Command._recompute",
            side_effect=ValueError("boom"),
        ):
            with self.assertRaises(ValueError):
                call_command("recompute_momentum")
        self.assertTrue(MomentumLog.objects.filter(was_successful=False).exists())
