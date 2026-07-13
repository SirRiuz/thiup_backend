# Django
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

# Libs
from app.methods.tags import create_tags, get_tags_list

# Models
from app.models.mask import Mask
from app.models.thread import Thread
from honeypot.models.black_list import BlackList

client = Client()


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadCardShapeTest(TestCase):
    """The trimmed thread-card contract: create inputs are not echoed back,
    internal/derived columns never leave the API and the mask block is an
    explicit {hash, is_online} allowlist."""

    def setUp(self):
        self.author = Mask.objects.create(hash="a" * 64, country_code="CO")
        self.thread = Thread.objects.create(
            mask=self.author,
            text="hola mundo",
            content={"blocks": [], "entityMap": {}},
            geohash="d2g6e5kju4hg",
        )

    def test_card_omits_write_only_and_internal_fields(self):
        r = client.get(f"/threads/{self.thread.uid}/")
        self.assertEqual(r.status_code, 200)
        card = r.data

        # What the frontend actually reads stays.
        for field in (
            "uid",
            "text",
            "responses_count",
            "media",
            "reactions",
            "last_reaction",
            "is_mine",
            "is_op",
            "create_at",
            "created_at_iso",
            "momentum_score",
        ):
            self.assertIn(field, card)

        # Create-only inputs and internal plumbing are gone.
        for field in ("content", "sub", "parent", "is_new", "reactions_count", "geohash", "geohash4", "id"):
            self.assertNotIn(field, card)

    def test_mask_block_is_hash_and_presence_only(self):
        r = client.get(f"/threads/{self.thread.uid}/")
        self.assertEqual(set(r.data["mask"].keys()), {"hash", "is_online"})


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadsListNormLookupsTest(TestCase):
    """/threads/?q= and ?tag= now match against the derived *_norm columns
    (indexed) — the accent/case-insensitive behavior must be identical to the
    old __unaccent__ lookups."""

    def setUp(self):
        self.author = Mask.objects.create(hash="b" * 64, country_code="CO")
        self.thread = Thread.objects.create(
            mask=self.author,
            text="En Bogotá llueve #perú",
            content={},
        )
        create_tags(self.thread, get_tags_list(self.thread.text))

    def test_query_matches_accent_insensitive(self):
        for q in ("bogota", "Bogotá", "BOGOTA"):
            r = client.get(f"/threads/?q={q}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual([t["uid"] for t in r.data["results"]], [self.thread.uid])

    def test_tag_matches_accent_insensitive_and_exact(self):
        for tag in ("peru", "perú", "PERU"):
            r = client.get(f"/threads/?tag={tag}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual([t["uid"] for t in r.data["results"]], [self.thread.uid])
        # Exact whole-tag semantics: a prefix must NOT match.
        r = client.get("/threads/?tag=per")
        self.assertEqual(r.data["results"], [])


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class MaskMiddlewareCacheTest(TestCase):
    """The mask lookup is served from LocMem after the first request: no
    app_mask round trip before every view."""

    def test_second_request_skips_mask_query(self):
        first = client.get("/reactions/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(Mask.objects.count(), 1)

        with CaptureQueriesContext(connection) as ctx:
            second = client.get("/reactions/")
        self.assertEqual(second.status_code, 200)

        mask_queries = [q["sql"] for q in ctx.captured_queries if "app_mask" in q["sql"]]
        self.assertEqual(mask_queries, [])
        self.assertEqual(Mask.objects.count(), 1)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class BlacklistCacheTest(TestCase):
    """The honeypot blacklist is cached but writes invalidate the cache via
    signals — banning and unbanning take effect on the very next request."""

    def test_blacklist_applies_and_lifts_immediately(self):
        self.assertEqual(client.get("/reactions/").status_code, 200)

        blocked = BlackList.objects.create(ip_address="127.0.0.1")
        self.assertEqual(client.get("/reactions/").status_code, 403)

        blocked.delete()
        self.assertEqual(client.get("/reactions/").status_code, 200)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ResponsesTreeQueryBoundTest(TestCase):
    """Nested replies serialize through with_card_relations: the reply tree
    must stay within a bounded query budget instead of ~5 queries per nested
    node (the legacy fallback)."""

    def setUp(self):
        self.author = Mask.objects.create(hash="c" * 64, country_code="CO")
        self.root = Thread.objects.create(mask=self.author, text="root", content={})
        for i in range(3):
            reply = Thread.objects.create(mask=self.author, text=f"reply {i}", content={}, sub=self.root)
            for j in range(2):
                Thread.objects.create(mask=self.author, text=f"sub {i}.{j}", content={}, sub=reply)

    def test_reply_tree_is_query_bounded(self):
        # Warm the mask-middleware cache so the count below is the endpoint's.
        client.get("/reactions/")

        with CaptureQueriesContext(connection) as ctx:
            r = client.get(f"/threads/{self.root.uid}/responses/")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 3)
        for reply in r.data["results"]:
            self.assertEqual(len(reply["responses"]), 2)
        # Legacy path: ~45+ queries for this tree (5 per nested node). The
        # fast path costs 1 subs query per node plus its prefetches.
        self.assertLess(len(ctx.captured_queries), 35)
