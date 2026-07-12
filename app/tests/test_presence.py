# Python
import hashlib

# Django
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

# Libs
from app.methods import presence

# Models
from app.models.mask import Mask
from app.models.thread import Thread

# Helpers
from app.tests.test_thread_files import decode_body

client = Client()

# The mask MaskMiddleware derives for the test client (SHA-256 of 127.0.0.1).
REQUESTER_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class PresenceTest(TestCase):
    """Ephemeral 'online now' indicator: passive marking in MaskMiddleware,
    LocMem-only, surfaced as mask.is_online in every thread payload."""

    def setUp(self):
        # LocMem persists across tests in the same process — isolate each one.
        cache.clear()

    def test_any_request_marks_the_requester_online(self):
        self.assertFalse(presence.is_online(REQUESTER_HASH))
        client.get("/threads/")
        self.assertTrue(presence.is_online(REQUESTER_HASH))

    def test_health_check_never_marks_presence(self):
        # /health/ is exempt from MaskMiddleware (ALB-era invariant kept for
        # the container health check): probes must not create presence.
        client.get("/health/")
        self.assertFalse(presence.is_online(REQUESTER_HASH))

    def test_presence_expires_with_the_ttl(self):
        presence.mark_online("a" * 64)
        self.assertTrue(presence.is_online("a" * 64))
        # Simulate the TTL elapsing (LocMem expiry is time-based; the key
        # vanishing is exactly what expiry does).
        cache.delete("online:" + "a" * 64)
        self.assertFalse(presence.is_online("a" * 64))

    def test_feed_exposes_is_online_per_author(self):
        online_author = Mask.objects.create(hash="b" * 64)
        offline_author = Mask.objects.create(hash="c" * 64)
        Thread.objects.create(text="online post", content={}, mask=online_author)
        Thread.objects.create(text="offline post", content={}, mask=offline_author)
        presence.mark_online(online_author.hash)

        body = decode_body(client.get("/threads/"))
        by_text = {row["text"]: row["mask"]["is_online"] for row in body["results"]}
        self.assertTrue(by_text["online post"])
        self.assertFalse(by_text["offline post"])

    def test_hover_card_exposes_is_online(self):
        target = Mask.objects.create(hash="d" * 64)
        presence.mark_online(target.hash)
        body = decode_body(client.get(f"/users/{target.hash}/"))
        self.assertTrue(body["is_online"])

    def test_empty_hash_is_never_online(self):
        presence.mark_online("")
        self.assertFalse(presence.is_online(""))

    def test_people_search_exposes_is_online(self):
        online_user = Mask.objects.create(hash="e" * 64)
        offline_user = Mask.objects.create(hash=("e" * 60) + "ffff")
        presence.mark_online(online_user.hash)

        body = decode_body(client.get("/search/?q=eeeeee&type=users"))
        by_hash = {row["hash"]: row["is_online"] for row in body["results"]}
        self.assertTrue(by_hash[online_user.hash])
        self.assertFalse(by_hash[offline_user.hash])
