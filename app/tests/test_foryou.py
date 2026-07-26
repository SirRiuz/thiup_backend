# Python
import base64
import json
from datetime import datetime, timedelta

# Libs
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from django.core.management import call_command

# Django
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status

from app.methods.tokens import encode_token

# Models
from app.models.mask import Mask
from app.models.media import ThreadFile
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation
from app.models.tag import Tag
from app.models.thread import Thread
from app.rest.serializers.thread_serializer import ThreadSerializer

client = Client()


def decode_body(response):
    """
    Decodes the response body, encrypted or not (depending on
    ENCRYPTED_RESPONSE in the test environment). Mirror of the frontend
    decrypt: X-Response-Payload = base64(key:iv) reversed, body reversed.
    """
    if "application/raw" not in (response.headers.get("Content-Type") or ""):
        return response.json()

    payload = response.headers["X-Response-Payload"][::-1]
    key_b64, iv_b64 = base64.b64decode(payload).decode().split(":")
    cipher = AES.new(
        base64.b64decode(key_b64),
        AES.MODE_CBC,
        base64.b64decode(iv_b64),
    )
    raw = base64.b64decode(response.content.decode()[::-1])
    return json.loads(unpad(cipher.decrypt(raw), AES.block_size).decode())


def encrypted_post(url, body, token):
    """POST con body CIFRADO — como el FE real (X-Request-Payload). El
    middleware lo descifra y la vista recibe JSON plano. Necesario ahora
    que ENCRYPTED_RESPONSE=True exige bodies cifrados en endpoints no
    exentos. Espejo del encrypt() del frontend."""
    import json as _json

    from Crypto.Cipher import AES as _AES
    from Crypto.Random import get_random_bytes as _rb
    from Crypto.Util.Padding import pad as _pad

    plain = body if isinstance(body, str) else _json.dumps(body)
    key, iv = _rb(16), _rb(16)
    ct = _AES.new(key, _AES.MODE_CBC, iv).encrypt(_pad(plain.encode(), 16))
    header = base64.b64encode(f"{base64.b64encode(key).decode()}:{base64.b64encode(iv).decode()}".encode()).decode()[
        ::-1
    ]
    return client.post(
        url,
        data=base64.b64encode(ct).decode()[::-1],
        content_type="application/raw",
        HTTP_X_REQUEST_PAYLOAD=header,
        HTTP_CLIENT_ASSERTION=token,
    )


def make_mask(tag) -> Mask:
    return Mask.objects.create(hash=f"hash-{tag}", country_code="CO")


def make_thread(mask, age_hours=0, sub=None, text="test") -> Thread:
    """Creates a thread and forces its age (create_at is auto_now_add)."""
    thread = Thread.objects.create(content={}, text=text, mask=mask, sub=sub)
    if age_hours:
        Thread.objects.filter(pk=thread.pk).update(create_at=timezone.now() - timedelta(hours=age_hours))
        thread.refresh_from_db()
    return thread


class RecomputeMomentumTest(TestCase):
    """Counting rules and formula of the management command."""

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")
        self.user_c = make_mask("c")
        # `love` is seeded by migration 0015; reuse it instead of recreating.
        self.reaction, _ = Reaction.objects.get_or_create(name="love", defaults={"emoji": "❤️"})

    def react(self, thread, mask):
        ReactionRelation.objects.create(thread=thread, mask=mask, reaction=self.reaction)

    def test_counting_rules_and_formula(self):
        """
        Author excluded from all signals, commenters DISTINCT,
        author dialogue counted, and momentum = points/(age+2)^1.5.
        """
        thread = make_thread(self.author, age_hours=10)

        # Reactions: author (excluded) + B + C => 2 unique.
        self.react(thread, self.author)
        self.react(thread, self.user_b)
        self.react(thread, self.user_c)

        # Comments: B comments 3 times (DISTINCT => 1), the author comments
        # on their own thread (excluded), C replies to B's comment
        # (depth 2 => counts). => 2 unique commenters.
        comment_b = make_thread(self.user_b, sub=thread)
        make_thread(self.user_b, sub=thread)
        make_thread(self.user_b, sub=thread)
        make_thread(self.author, sub=thread)
        make_thread(self.user_c, sub=comment_b)

        # Author dialogue: the author replies to B's comment
        # (1 distinct person replied to).
        make_thread(self.author, sub=comment_b)

        call_command("recompute_momentum")
        thread.refresh_from_db()

        self.assertEqual(thread.unique_reactors_count, 2)
        self.assertEqual(thread.unique_commenters_count, 2)

        # points = 2 + 2*3 + 1*5 = 13 ; momentum = 13 / (10+2)^1.5
        expected = 13 / ((10 + 2) ** 1.5)
        self.assertAlmostEqual(thread.momentum_score, expected, places=3)

    def test_author_talking_alone_counts_zero(self):
        """A thread where only the author talks/reacts => 0 in everything."""
        thread = make_thread(self.author, age_hours=5)
        self.react(thread, self.author)
        own_comment = make_thread(self.author, sub=thread)
        make_thread(self.author, sub=own_comment)  # self-reply

        call_command("recompute_momentum")
        thread.refresh_from_db()

        self.assertEqual(thread.unique_reactors_count, 0)
        self.assertEqual(thread.unique_commenters_count, 0)
        self.assertEqual(thread.momentum_score, 0)

    def test_active_window_skips_old_posts(self):
        """Posts outside the --days window are not recalculated."""
        old = make_thread(self.author, age_hours=72)  # 3 days
        self.react(old, self.user_b)

        call_command("recompute_momentum", days=1)
        old.refresh_from_db()
        self.assertEqual(old.unique_reactors_count, 0)  # untouched

        call_command("recompute_momentum", days=30)
        old.refresh_from_db()
        self.assertEqual(old.unique_reactors_count, 1)

    def test_command_is_idempotent(self):
        """Two consecutive runs leave the same values."""
        thread = make_thread(self.author, age_hours=4)
        self.react(thread, self.user_b)
        make_thread(self.user_b, sub=thread)

        call_command("recompute_momentum")
        thread.refresh_from_db()
        first = (
            thread.unique_reactors_count,
            thread.unique_commenters_count,
        )

        call_command("recompute_momentum")
        thread.refresh_from_db()
        self.assertEqual(
            first,
            (
                thread.unique_reactors_count,
                thread.unique_commenters_count,
            ),
        )

    def test_each_run_writes_a_momentum_log(self):
        """Each run leaves its record in the MomentumLog log."""
        from app.models.momentum_log import MomentumLog

        thread = make_thread(self.author, age_hours=4)
        self.react(thread, self.user_b)

        call_command("recompute_momentum", days=15)
        call_command("recompute_momentum", days=15)

        logs = MomentumLog.objects.order_by("create_at")
        self.assertEqual(logs.count(), 2)
        last = logs.last()
        self.assertTrue(last.was_successful)
        self.assertEqual(last.window_days, 15)
        self.assertEqual(last.processed_count, 1)
        self.assertEqual(last.error, "")


class ForYouViewTest(TestCase):
    """Threshold, momentum ordering and fallback of the For You endpoint."""

    PAGE_SIZE = 25  # REST_FRAMEWORK.PAGE_SIZE

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")
        # `love` is seeded by migration 0015; reuse it instead of recreating.
        self.reaction, _ = Reaction.objects.get_or_create(name="love", defaults={"emoji": "❤️"})

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def get_foryou(self):
        # POST: the feed params go in the body (privacy: out of the URL
        # logs); empty body = cold start.
        response = encrypted_post(
            "/threads/foryou/",
            {},
            self.__get_client_token(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return decode_body(response)

    def test_pagination_works_with_post(self):
        """?page=N in query (short, not sensitive) + body with the params:
        DRF pagination works the same; GET is no longer allowed."""
        for i in range(self.PAGE_SIZE + 5):
            thread = make_thread(self.author, age_hours=6, text=f"p-{i}")
            make_thread(self.user_b, sub=thread)
        call_command("recompute_momentum")

        token = self.__get_client_token()
        page1 = decode_body(encrypted_post("/threads/foryou/", {}, token))
        page2 = decode_body(encrypted_post("/threads/foryou/?page=2", {}, self.__get_client_token()))

        self.assertEqual(len(page1["results"]), self.PAGE_SIZE)
        self.assertEqual(len(page2["results"]), 5)
        uids1 = {p["uid"] for p in page1["results"]}
        uids2 = {p["uid"] for p in page2["results"]}
        self.assertEqual(uids1 & uids2, set())  # no overlap
        self.assertIsNotNone(page1["next"])

        # GET → 405 (the transport is now POST)
        response = client.get("/threads/foryou/", HTTP_CLIENT_ASSERTION=self.__get_client_token())
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_threshold_and_momentum_ordering(self):
        """
        With >= PAGE_SIZE posts meeting the threshold: an old post with no
        interaction does NOT enter, a new one (<2h) DOES (grace), and the
        one with the highest momentum comes first.
        """
        # PAGE_SIZE+1 posts with 1 commenter each (they meet the threshold).
        qualifying = []
        for i in range(self.PAGE_SIZE + 1):
            thread = make_thread(self.author, age_hours=12, text=f"q-{i}")
            make_thread(self.user_b, sub=thread)
            qualifying.append(thread)

        # The most recent of the qualifying ones dominates by decay: same
        # numerator, lower age => higher momentum.
        hot = make_thread(self.author, age_hours=3, text="hot")
        make_thread(self.user_b, sub=hot)

        # Old (>2h) with no interaction: must NOT enter.
        stale = make_thread(self.author, age_hours=10, text="stale")
        # New (<2h) with no interaction: DOES enter (grace window).
        fresh = make_thread(self.author, age_hours=1, text="fresh")

        call_command("recompute_momentum")
        body = self.get_foryou()

        # qualifying (26) + hot + fresh = 28; stale stays out.
        self.assertEqual(body["count"], self.PAGE_SIZE + 3)
        uids = [post["uid"] for post in body["results"]]
        self.assertNotIn(stale.uid, uids)
        self.assertEqual(uids[0], hot.uid)

    def test_fallback_fills_with_newest(self):
        """
        With less than a page meeting the threshold, the feed is filled
        with the rest (newest), qualifying first.
        """
        loud = make_thread(self.author, age_hours=12, text="loud")
        make_thread(self.user_b, sub=loud)

        quiet_old = make_thread(self.author, age_hours=30, text="old-1")
        quiet_older = make_thread(self.author, age_hours=40, text="old-2")

        call_command("recompute_momentum")
        body = self.get_foryou()

        # Nothing is lost: the feed also serves those that don't qualify.
        self.assertEqual(body["count"], 3)
        uids = [post["uid"] for post in body["results"]]
        self.assertEqual(uids[0], loud.uid)
        # Fill in newest order.
        self.assertEqual(uids[1:], [quiet_old.uid, quiet_older.uid])


def tag_thread(thread, name):
    """Creates the Tag row the way create_tags does (lowercase + name_norm)."""
    Tag.objects.create(name=name, name_norm=name, thread=thread)


class ForYouPersonalizedTest(TestCase):
    """70/30 mix with ?tags=, no duplicates, and cold start intact."""

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def get_foryou(self, tags=None):
        body = {"tags": tags} if tags else {}
        response = encrypted_post("/threads/foryou/", body, self.__get_client_token())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return decode_body(response)

    def __make_qualifying(self, text, age_hours, tag=None):
        """Root post that meets the threshold (1 commenter ≠ author)."""
        thread = make_thread(self.author, age_hours=age_hours, text=text)
        make_thread(self.user_b, sub=thread)
        if tag:
            tag_thread(thread, tag)
        return thread

    def test_mix_is_momentum_ordered_without_duplicates(self):
        """
        STEP 1 + STEP 4 of the algorithm: the candidate composition is
        70/30 (user tags + global discovery), but the feed ORDER is
        ALWAYS momentum DESC over all candidates — a single monotonic
        list, without interleaved "windows" — and with no repeated posts.
        """
        # 10 tagged (12h) and 10 untagged (6h → more momentum). Both groups
        # fit within their quotas (350/150): all 20 are candidates.
        tagged = [self.__make_qualifying(f"gato {i} #cats", 12, tag="cats") for i in range(10)]
        untagged = [self.__make_qualifying(f"global {i}", 6) for i in range(10)]

        call_command("recompute_momentum")
        body = self.get_foryou(tags="cats")

        uids = [post["uid"] for post in body["results"]]
        # No duplicates in the mixed feed.
        self.assertEqual(len(uids), len(set(uids)))

        # MONOTONIC ORDER: momentum_final (base × boosts, computed when
        # serving) strictly non-increasing across the page.
        momenta = [post["momentum_final"] for post in body["results"]]
        self.assertEqual(momenta, sorted(momenta, reverse=True))

        # COMPOSITION: both groups present (70% affinity + 30%
        # anti-bubble discovery). With this data the untagged ones (6h)
        # have more momentum -> they go first; the tagged ones after.
        tagged_uids = {t.uid for t in tagged}
        untagged_uids = {t.uid for t in untagged}
        self.assertTrue(all(u in untagged_uids for u in uids[:10]))
        self.assertTrue(all(u in tagged_uids for u in uids[10:20]))
        # Nothing is lost: all 20 posts are in the feed.
        self.assertEqual(body["count"], 20)

    def test_cold_start_without_tags_unchanged(self):
        """Without ?tags= the feed is the pure global top (PHASE 1 intact)."""
        high = self.__make_qualifying("hot #cats", 3, tag="cats")
        low = self.__make_qualifying("cold", 40)

        call_command("recompute_momentum")
        body = self.get_foryou()

        uids = [post["uid"] for post in body["results"]]
        self.assertEqual(uids[0], high.uid)
        self.assertIn(low.uid, uids)

    def test_tags_are_normalized_and_capped(self):
        """?tags accepts '#', uppercase and accents; ignores empties."""
        thread = self.__make_qualifying("hola #perú", 5, tag="peru")

        call_command("recompute_momentum")
        # '#Perú' (with accent and #) must match the Tag name_norm 'peru'.
        body = self.get_foryou(tags="#Perú,, ")

        uids = [post["uid"] for post in body["results"]]
        self.assertIn(thread.uid, uids)

    def test_no_profile_is_persisted_server_side(self):
        """
        PRIVACY: the request tags are ephemeral — after the request there
        is no new row associating tags with a mask (the only tables touched
        are the content ones, there is no profile table).
        """
        self.__make_qualifying("hola #cats", 5, tag="cats")
        call_command("recompute_momentum")

        # Warm-up: the middleware creates the test client's mask on its
        # FIRST request — we trigger it before taking the baseline.
        self.get_foryou()

        tags_before = Tag.objects.count()
        masks_before = Mask.objects.count()

        self.get_foryou(tags="cats,dogs,birds")

        # Neither new Tags nor new masks from having sent tags. (The test
        # client's mask is created by the middleware on ANY request — that's
        # why we compare against a previous request, not against zero.)
        self.assertEqual(Tag.objects.count(), tags_before)
        self.assertEqual(Mask.objects.count(), masks_before)


class ForYouRegionBoostTest(TestCase):
    """Regional boost ×1.5 at query time (STEP 4)."""

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def get_foryou(self, body=None):
        response = encrypted_post(
            "/threads/foryou/",
            body or {},
            self.__get_client_token(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return decode_body(response)

    def __make_regional(self, text, age_hours, region):
        thread = make_thread(self.author, age_hours=age_hours, text=text)
        make_thread(self.user_b, sub=thread)  # meets the threshold
        Thread.objects.filter(pk=thread.pk).update(region=region)
        thread.refresh_from_db()
        return thread

    def test_region_boost_reorders_without_mutating_base(self):
        """
        A post from the reader's region with LESS base momentum beats one
        from another region with more — but the served momentum_score
        remains the BASE (the mutation is only of the order, per query).
        """
        # local: older → less base momentum. foreign: newer.
        local = self.__make_regional("local #co", age_hours=8, region="CO")
        foreign = self.__make_regional("foreign #us", age_hours=6, region="US")

        call_command("recompute_momentum")
        local.refresh_from_db()
        foreign.refresh_from_db()
        # Precondition: without boost, foreign dominates.
        self.assertGreater(foreign.momentum_score, local.momentum_score)
        # And with the ×1.5 boost local beats it (the test scenario).
        self.assertGreater(local.momentum_score * 1.5, foreign.momentum_score)

        # No region → order by base: foreign first.
        body = self.get_foryou()
        uids = [p["uid"] for p in body["results"]]
        self.assertEqual(uids[0], foreign.uid)

        # With ?region=CO (the test client's mask is "Unknow" → the BA
        # accepts the declared one) → local first despite less base.
        body = self.get_foryou({"region": "CO"})
        uids = [p["uid"] for p in body["results"]]
        self.assertEqual(uids[0], local.uid)

        # served momentum_score = BASE intact; served momentum_final =
        # base × 1.5 ONLY for the post from my region (query-time). The DB
        # is never mutated by the boost.
        by_uid = {p["uid"]: p for p in body["results"]}
        self.assertAlmostEqual(by_uid[local.uid]["momentum_score"], local.momentum_score, places=6)
        self.assertAlmostEqual(by_uid[local.uid]["momentum_final"], local.momentum_score * 1.5, places=6)
        # Region different from mine → final == base (no increase).
        self.assertAlmostEqual(by_uid[foreign.uid]["momentum_final"], foreign.momentum_score, places=6)
        local.refresh_from_db()
        self.assertAlmostEqual(
            local.momentum_score,
            Thread.objects.get(pk=local.pk).momentum_score,
        )

    def test_declared_locale_is_normalized_without_geoip(self):
        """
        WITHOUT GeoIP: the backend only sanitizes what the FE declares
        (navigator.language). Garbage/absent → no boost / default.
        """
        from app.utils.locale import normalize_language, normalize_region

        self.assertEqual(normalize_region("co"), "CO")
        self.assertEqual(normalize_region(" CO "), "CO")
        self.assertEqual(normalize_region(""), "")
        self.assertEqual(normalize_region("<x>!"), "")

        self.assertEqual(normalize_language("Es"), "es")
        self.assertEqual(normalize_language("EN "), "en")
        self.assertEqual(normalize_language("", default="es"), "es")
        self.assertEqual(normalize_language("<js>", default="es"), "es")
        self.assertEqual(normalize_language("", default=""), "")


class ThreadLocaleCreationTest(TestCase):
    """The thread is tagged with language/region declared by the FE."""

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def __create(self, extra):
        body = {
            "media": [],
            "text": "hola mundo",
            "content": {"blocks": [], "entityMap": {}},
            **extra,
        }
        response = encrypted_post(
            "/threads/",
            body,
            self.__get_client_token(),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return Thread.objects.get(uid=decode_body(response)["uid"])

    def test_language_and_region_stored_normalized(self):
        thread = self.__create({"language": "Es", "region": "co"})
        self.assertEqual(thread.language, "es")
        self.assertEqual(thread.region, "CO")

    def test_geohash_stored_normalized_when_shared(self):
        """
        Author with opt-in → the geohash arrives in the payload and is
        stored COARSE: truncated to precision 5 even if the client sends
        finer (privacy), lowercased.
        """
        thread = self.__create({"geohash": "D2G6EXTRA"})
        self.assertEqual(thread.geohash, "d2g6e")

    def test_no_geohash_means_null_graceful(self):
        """Without opt-in (or garbage) → null; creation is never blocked."""
        thread = self.__create({})
        self.assertIsNone(thread.geohash)
        thread = self.__create({"geohash": "<x>!"})
        self.assertIsNone(thread.geohash)
        thread = self.__create({"geohash": "ab"})  # too short
        self.assertIsNone(thread.geohash)

    def test_missing_or_garbage_locale_falls_back(self):
        """navigator.language absent/weird → default 'es' and no region."""
        thread = self.__create({})
        self.assertEqual(thread.language, "es")
        self.assertEqual(thread.region, "")

        thread = self.__create({"language": "<x>!", "region": "<y>!"})
        self.assertEqual(thread.language, "es")
        self.assertEqual(thread.region, "")


class ForYouLanguageFilterTest(TestCase):
    """STEP 2: hard language filter with fallback (never an empty feed)."""

    PAGE_SIZE = 25

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def get_foryou(self, body=None):
        response = encrypted_post(
            "/threads/foryou/",
            body or {},
            self.__get_client_token(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return decode_body(response)

    def __make_lang_post(self, text, language):
        thread = make_thread(self.author, age_hours=6, text=text)
        make_thread(self.user_b, sub=thread)  # meets the threshold
        Thread.objects.filter(pk=thread.pk).update(language=language)
        thread.refresh_from_db()
        return thread

    def test_hard_filter_when_enough_content(self):
        """With enough content in the language, the rest do NOT enter."""
        en_posts = [self.__make_lang_post(f"english {i}", "en") for i in range(self.PAGE_SIZE + 1)]
        es_post = self.__make_lang_post("hola", "es")

        call_command("recompute_momentum")
        body = self.get_foryou({"lang": "en"})

        uids = {p["uid"] for p in body["results"]}
        self.assertNotIn(es_post.uid, uids)
        self.assertEqual(body["count"], len(en_posts))

    def test_fallback_to_global_when_scarce(self):
        """
        Very few posts in the reader's language (< page) → the filter is
        removed and the global top is served: the feed is never empty.
        """
        self.__make_lang_post("hola 1", "es")
        self.__make_lang_post("hola 2", "es")
        en_post = self.__make_lang_post("english", "en")

        call_command("recompute_momentum")
        body = self.get_foryou({"lang": "es"})

        uids = {p["uid"] for p in body["results"]}
        self.assertIn(en_post.uid, uids)  # global fallback included the 'en'
        self.assertEqual(body["count"], 3)

    def test_model_save_canonicalizes_locale(self):
        """
        Any writer (admin included) that saves "ES"/"co" gets
        normalized by Thread.save() — the filter compares exact.
        """
        thread = make_thread(make_mask("x"), text="hola")
        thread.language = "ES"
        thread.region = "co"
        thread.save()
        thread.refresh_from_db()
        self.assertEqual(thread.language, "es")
        self.assertEqual(thread.region, "CO")

    def test_no_lang_param_means_no_filter(self):
        """Clients without ?lang= → global behavior (no filter)."""
        self.__make_lang_post("hola", "es")
        self.__make_lang_post("english", "en")

        call_command("recompute_momentum")
        body = self.get_foryou()
        self.assertEqual(body["count"], 2)


class ForYouAffinityBoostTest(TestCase):
    """affinity_boost = 1 + 0.35 × matching_tags (cap 3)."""

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def get_foryou(self, body=None):
        response = encrypted_post(
            "/threads/foryou/",
            body or {},
            self.__get_client_token(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return decode_body(response)

    def __make_post(self, text, age_hours, tag_names=()):
        thread = make_thread(self.author, age_hours=age_hours, text=text)
        make_thread(self.user_b, sub=thread)  # meets the threshold
        for name in tag_names:
            tag_thread(thread, name)
        return thread

    def test_affinity_boost_reorders_the_top(self):
        """
        A post with 3 matching tags and LESS base momentum beats the
        global top (×2.05); with 1 tag (×1.35) it doesn't make it. The
        served momentum_final reflects the exact ratios.
        """
        # base: untagged(6h)=0.1326 > tagged(10h)=0.0722
        untagged = self.__make_post("global fuerte", 6)
        tagged3 = self.__make_post("match total #cats #dogs #birds", 10, tag_names=("cats", "dogs", "birds"))
        tagged1 = self.__make_post("match parcial #cats", 10, tag_names=("cats",))

        call_command("recompute_momentum")
        body = self.get_foryou({"tags": ["cats", "dogs", "birds"]})

        uids = [p["uid"] for p in body["results"]]
        # tagged3: 0.0722×2.05=0.148 > untagged 0.1326 > tagged1 ×1.35=0.097
        self.assertEqual(uids[:3], [tagged3.uid, untagged.uid, tagged1.uid])

        by_uid = {p["uid"]: p for p in body["results"]}
        ratio = lambda p: p["momentum_final"] / p["momentum_score"]
        self.assertAlmostEqual(ratio(by_uid[tagged3.uid]), 2.05, places=6)
        self.assertAlmostEqual(ratio(by_uid[tagged1.uid]), 1.35, places=6)
        self.assertAlmostEqual(ratio(by_uid[untagged.uid]), 1.0, places=6)

    def test_affinity_boost_caps_at_three_matches(self):
        """5 matching tags → same cap as 3 (×2.05)."""
        names = ("a1", "b2", "c3", "d4", "e5")
        post = self.__make_post("todos los tags " + " ".join(f"#{n}" for n in names), 6, tag_names=names)

        call_command("recompute_momentum")
        body = self.get_foryou({"tags": list(names)})

        match = next(p for p in body["results"] if p["uid"] == post.uid)
        self.assertAlmostEqual(match["momentum_final"] / match["momentum_score"], 2.05, places=6)

    def test_low_momentum_tagged_post_enters_candidates(self):
        """
        A post aligned with your tags but with low momentum DOES enter the
        pool (union) and the boost can raise it — it isn't left out for not
        being in the global top.
        """
        # Many strong global posts + one weak tagged one (older).
        for i in range(10):
            self.__make_post(f"global {i}", 5)
        weak = self.__make_post("nicho #cats", 30, tag_names=("cats",))

        call_command("recompute_momentum")
        body = self.get_foryou({"tags": "cats"})

        uids = [p["uid"] for p in body["results"]]
        self.assertIn(weak.uid, uids)


class CloseYouViewTest(TestCase):
    """Close You: expandable radius, adaptive precision and ring-based boost."""

    CENTER = None  # computed in setUp from real Medellín coords

    def setUp(self):
        from app.utils.geo import encode_geohash

        self.author = make_mask("author")
        self.user_b = make_mask("b")
        # Medellín, from coordinates (real geography, no assumptions).
        self.CENTER = encode_geohash(6.2442, -75.5812)

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def post_closeyou(self, body, expected=status.HTTP_200_OK):
        response = encrypted_post(
            "/threads/closeyou/",
            body,
            self.__get_client_token(),
        )
        self.assertEqual(response.status_code, expected)
        return decode_body(response) if expected == status.HTTP_200_OK else None

    def __make_geo(self, text, age_hours, geohash, qualifying=True):
        thread = make_thread(self.author, age_hours=age_hours, text=text)
        if qualifying:
            make_thread(self.user_b, sub=thread)  # meets the threshold
        thread.geohash = geohash
        thread.save()  # save() derives geohash4
        return thread

    def test_ring_boost_and_far_exclusion(self):
        """
        Boost decreasing with distance: reader's cell ×1.35, an
        intermediate cell receives less boost (according to its normalized
        distance); another city does NOT enter. No affinity/region.
        """
        from app.utils.geo import cells_for_radius

        ring_by_cell, precision = cells_for_radius(self.CENTER, 15)
        self.assertEqual(precision, 5)
        # An intermediate cell of the grid (neither the center nor the edge).
        mid_cell, mid_ring = next((cell, ring) for cell, ring in ring_by_cell.items() if 0 < ring < 1)

        center_post = self.__make_geo("centro", 6, self.CENTER)
        mid_post = self.__make_geo("intermedio", 7, mid_cell)
        far_post = self.__make_geo("otra ciudad", 6, "9g3qr")  # CDMX

        call_command("recompute_momentum")
        body = self.post_closeyou({"geohash": self.CENTER})

        uids = [p["uid"] for p in body["results"]]
        self.assertIn(center_post.uid, uids)
        self.assertIn(mid_post.uid, uids)
        self.assertNotIn(far_post.uid, uids)

        by_uid = {p["uid"]: p for p in body["results"]}
        ratio = lambda p: p["momentum_final"] / p["momentum_score"]
        self.assertAlmostEqual(ratio(by_uid[center_post.uid]), 1.35, places=6)
        # expected boost of the intermediate cell, from its real distance.
        expected_mid = 1 + 0.35 * (1 - mid_ring)
        self.assertAlmostEqual(ratio(by_uid[mid_post.uid]), expected_mid, places=6)
        self.assertLess(ratio(by_uid[mid_post.uid]), 1.35)

    def test_radius_expansion_includes_farther_posts(self):
        """
        A post at ~40 km: out with the default (15 km, precision 5);
        in when expanding to 100 km (precision 4 via indexed geohash4).
        """
        from app.utils.geo import encode_geohash

        # ~40 km north of Medellín (0.36° of latitude).
        far_cell = encode_geohash(6.2442 + 0.36, -75.5812)
        nearby = self.__make_geo("a 40km", 5, far_cell)

        call_command("recompute_momentum")

        body = self.post_closeyou({"geohash": self.CENTER})
        self.assertNotIn(nearby.uid, [p["uid"] for p in body["results"]])

        body = self.post_closeyou({"geohash": self.CENTER, "radius_km": 100})
        self.assertIn(nearby.uid, [p["uid"] for p in body["results"]])

    def test_radius_clamp_and_default(self):
        """Clamp 1-100; invalid/absent → 15."""
        from app.rest.threads import parse_closeyou_radius

        self.assertEqual(parse_closeyou_radius(None), 15.0)
        self.assertEqual(parse_closeyou_radius("garbage"), 15.0)
        self.assertEqual(parse_closeyou_radius(5000), 100.0)
        self.assertEqual(parse_closeyou_radius(0.2), 1.0)
        self.assertEqual(parse_closeyou_radius(30), 30.0)

    def test_cells_stay_bounded_across_radii(self):
        """The number of cells stays bounded across the WHOLE 1-100 km range."""
        from app.utils.geo import cells_for_radius

        for radius in (1, 15, 25, 26, 50, 100):
            ring_by_cell, precision = cells_for_radius(self.CENTER, radius)
            self.assertLessEqual(len(ring_by_cell), 169)
            self.assertEqual(precision, 5 if radius <= 25 else 4)

    def test_filler_is_local_newest_never_global(self):
        """
        The fallback fills with LOCAL newest (no threshold) — but NEVER
        with posts from another area nor without geolocation.
        """
        local_quiet = self.__make_geo("local sin ruido", 30, self.CENTER, qualifying=False)
        global_post = make_thread(self.author, age_hours=5, text="global")
        make_thread(self.user_b, sub=global_post)

        call_command("recompute_momentum")
        body = self.post_closeyou({"geohash": self.CENTER})

        uids = [p["uid"] for p in body["results"]]
        self.assertIn(local_quiet.uid, uids)
        self.assertNotIn(global_post.uid, uids)

    def test_reader_geohash_required_and_legacy_cells(self):
        """Without geohash (or garbage) → 400. Legacy cells[] still accepted.
        The reader's cells are ephemeral: nothing new is persisted."""
        self.post_closeyou({}, expected=status.HTTP_400_BAD_REQUEST)
        self.post_closeyou({"geohash": "<x>!"}, expected=status.HTTP_400_BAD_REQUEST)

        self.__make_geo("uno", 5, self.CENTER)
        call_command("recompute_momentum")
        # Compat: old clients with cells[] (the first one = center).
        body = self.post_closeyou({"cells": [self.CENTER]})
        self.assertEqual(len(body["results"]), 1)

        threads_before = Thread.objects.count()
        self.post_closeyou({"geohash": self.CENTER, "radius_km": 100})
        self.assertEqual(Thread.objects.count(), threads_before)


class SearchTopOrderingTest(TestCase):
    """La pestaña 'Destacados' del buscador ordena por momentum precomputado."""

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def search(self, ordering):
        r = client.get(
            f"/search/?q=lluvia&type=posts&ordering={ordering}",
            HTTP_CLIENT_ASSERTION=self.__get_client_token(),
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        return decode_body(r)

    def test_top_orders_by_momentum_latest_chronological_no_filter(self):
        # 3 posts que coinciden con el término. 'old' es el más nuevo
        # (más arriba en latest) pero sin interacción → momentum 0;
        # 'hot' es más viejo pero con actividad → más momentum.
        old = make_thread(self.author, age_hours=1, text="lluvia hoy")
        hot = make_thread(self.author, age_hours=10, text="lluvia fuerte")
        make_thread(self.user_b, sub=hot)  # comentarista → momentum
        quiet = make_thread(self.author, age_hours=5, text="lluvia ayer")

        call_command("recompute_momentum")

        top = self.search("-momentum_score")
        latest = self.search("-create_at")

        # Sin filtrar: los 3 aparecen en ambos órdenes (sin umbral).
        self.assertEqual(top["count"], 3)
        self.assertEqual(latest["count"], 3)

        top_uids = [p["uid"] for p in top["results"]]
        latest_uids = [p["uid"] for p in latest["results"]]

        # top: el de mayor momentum primero; los de momentum 0 al final
        # ordenados por fecha (old más nuevo que quiet).
        self.assertEqual(top_uids[0], hot.uid)
        self.assertEqual(top_uids[1:], [old.uid, quiet.uid])
        # latest: cronológico puro (old es el más reciente).
        self.assertEqual(latest_uids[0], old.uid)


class SearchByMaskTest(TestCase):
    """Buscar el id público de una máscara incluye los hilos de ese autor
    en top/latest — solo coincidencia EXACTA del id público (hash[0:6])."""

    def __get_client_token(self) -> str:
        from datetime import datetime as _dt

        return encode_token({"timestamp": _dt.now().__str__()})

    def search(self, q, ordering="-momentum_score"):
        r = client.get(
            f"/search/?q={q}&type=posts&ordering={ordering}",
            HTTP_CLIENT_ASSERTION=self.__get_client_token(),
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        return decode_body(r)

    def setUp(self):
        # Máscaras con hash conocido (los 6 primeros = id público).
        self.author = Mask.objects.create(hash="abcdef" + "0" * 58, country_code="CO")
        self.other = Mask.objects.create(hash="999999" + "1" * 58, country_code="CO")
        # Hilo del autor SIN el término en el texto (solo matchea por autor).
        self.by_author = make_thread(self.author, age_hours=5, text="hola sin palabra clave")
        # Hilo de otro autor que SÍ contiene "abcdef" en el texto.
        self.by_text = make_thread(self.other, age_hours=5, text="esto menciona abcdef en el cuerpo")
        call_command("recompute_momentum")

    def test_exact_mask_includes_author_threads(self):
        body = self.search("abcdef")
        uids = [p["uid"] for p in body["results"]]
        # Aparece el hilo del autor (por máscara) Y el que matchea por texto.
        self.assertIn(self.by_author.uid, uids)
        self.assertIn(self.by_text.uid, uids)

    def test_at_prefix_same_result(self):
        plain = {p["uid"] for p in self.search("abcdef")["results"]}
        at = {p["uid"] for p in self.search("@abcdef")["results"]}
        self.assertEqual(plain, at)

    def test_partial_mask_does_not_trigger_author(self):
        # 'abcd' (4 chars) no es un id público completo → NO incluye al
        # autor por máscara (su texto no tiene 'abcd' → no aparece).
        uids = {p["uid"] for p in self.search("abcd")["results"]}
        self.assertNotIn(self.by_author.uid, uids)

    def test_internal_hash_not_searchable(self):
        # El hash interno completo NO matchea por id público (>6 chars) y
        # su texto no lo contiene → el autor no aparece por su hash interno.
        uids = {p["uid"] for p in self.search(self.author.hash[:40])["results"]}
        self.assertNotIn(self.by_author.uid, uids)

    def test_no_duplicates_when_text_and_author_match(self):
        # A thread by the author that ALSO contains their own id in the text:
        dual = make_thread(self.author, age_hours=3, text="mi id es abcdef")
        call_command("recompute_momentum")
        uids = [p["uid"] for p in self.search("abcdef")["results"]]
        self.assertEqual(uids.count(dual.uid), 1)


def make_file(thread, is_video=False, is_active=True, tag="x"):
    """Attaches a confirmed-style media file to a thread (metadata carries
    the dimensions/color like the confirm endpoint stores them)."""
    return ThreadFile.objects.create(
        thread=thread,
        mask=thread.mask,
        is_video=is_video,
        is_active=is_active,
        file_key=f"m/aa/bb/{tag}.webp",
        file_url=f"https://cdn.test/m/aa/bb/{tag}.webp",
        metadata={"width": 1080, "height": 1350, "target_color": "161c1e"},
    )


class SearchMediaTest(TestCase):
    """The Media tab: gallery of the files of the threads the search matches."""

    def setUp(self):
        self.author = make_mask("author")
        self.viewer = make_mask("viewer")

    def __get_client_token(self) -> str:
        return encode_token({"timestamp": datetime.now().__str__()})

    def search(self, q, search_type="media"):
        r = client.get(
            f"/search/?q={q}&type={search_type}",
            HTTP_CLIENT_ASSERTION=self.__get_client_token(),
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        return decode_body(r)

    def test_media_one_tile_per_matching_thread_with_media_count(self):
        # A matching thread with 2 files → ONE tile (its FIRST file, upload
        # order) carrying media_count=2 (the client's carousel mark); a
        # non-matching thread never appears.
        hit = make_thread(self.author, age_hours=1, text="lluvia fuerte")
        first = make_file(hit, tag="one")
        make_file(hit, is_video=True, tag="two")
        miss = make_thread(self.author, age_hours=1, text="sol radiante")
        make_file(miss, tag="three")

        body = self.search("lluvia")

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["counts"]["media"], 1)
        items = body["results"]
        self.assertEqual([i["uid"] for i in items], [first.uid])
        # Gallery item shape: what the frontend's mapMedia consumes.
        self.assertEqual(items[0]["file"], first.file_url)
        self.assertEqual(items[0]["width"], 1080)
        self.assertEqual(items[0]["height"], 1350)
        self.assertEqual(items[0]["target_color"], "161c1e")
        self.assertFalse(items[0]["is_video"])
        self.assertEqual(items[0]["media_count"], 2)
        # Parent card attached for the preview panel / navigation.
        self.assertEqual(items[0]["thread"]["uid"], hit.uid)

    def test_media_excludes_inactive_files_and_reply_media(self):
        root = make_thread(self.author, age_hours=1, text="lluvia hoy")
        kept = make_file(root, tag="kept")
        make_file(root, is_active=False, tag="soft-deleted")
        # Replies are outside the posts universe (root-only), even if they match.
        reply = make_thread(self.viewer, sub=root, text="lluvia tambien")
        make_file(reply, tag="reply-file")

        body = self.search("lluvia")
        self.assertEqual([i["uid"] for i in body["results"]], [kept.uid])
        # media_count also ignores inactive files (1 visible, not 2).
        self.assertEqual(body["results"][0]["media_count"], 1)

    def test_media_ordered_by_parent_momentum(self):
        quiet = make_thread(self.author, age_hours=1, text="lluvia hoy")
        quiet_file = make_file(quiet, tag="quiet")
        hot = make_thread(self.author, age_hours=10, text="lluvia fuerte")
        hot_file = make_file(hot, tag="hot")
        make_thread(self.viewer, sub=hot)  # commenter → momentum
        call_command("recompute_momentum")

        body = self.search("lluvia")
        # hot is older but has momentum → its file leads the gallery.
        self.assertEqual(
            [i["uid"] for i in body["results"]],
            [hot_file.uid, quiet_file.uid],
        )

    def test_counts_only_active_tab_and_empty_query(self):
        # Only the ACTIVE tab is counted (the frontend renders no tab
        # badges, so the other three COUNT queries were per-request cost):
        # the inactive keys stay present at 0 — the shape is frozen.
        thread = make_thread(self.author, age_hours=1, text="lluvia hoy")
        make_file(thread, tag="one")

        posts_body = self.search("lluvia", search_type="posts")
        self.assertEqual(posts_body["counts"]["posts"], 1)
        self.assertEqual(posts_body["counts"]["media"], 0)

        media_body = self.search("lluvia", search_type="media")
        self.assertEqual(media_body["counts"]["media"], 1)
        self.assertEqual(media_body["counts"]["posts"], 0)

        empty_body = self.search("", search_type="media")
        self.assertEqual(empty_body["counts"]["media"], 0)
        self.assertEqual(empty_body["results"], [])


class RequestCryptoTest(TestCase):
    """Middleware de descifrado de request + endpoint /config/."""

    def __token(self):
        from datetime import datetime as _dt

        return encode_token({"timestamp": _dt.now().__str__()})

    def test_encrypted_body_reaches_view_as_plain(self):
        # Crear hilo con body CIFRADO → la vista lo procesa (201).
        author = make_mask("enc")
        body = {
            "media": [],
            "text": "cuerpo cifrado",
            "content": {"blocks": [], "entityMap": {}},
        }
        before = Thread.objects.count()
        r = encrypted_post("/threads/", body, self.__token())
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Thread.objects.count(), before + 1)

    def test_plain_body_rejected_when_flag_on(self):
        # Enforcement: body en claro en endpoint no exento → 400.
        r = client.post("/threads/foryou/", {}, content_type="application/json", HTTP_CLIENT_ASSERTION=self.__token())
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_envelope_rejected(self):
        # Sobre cifrado basura → 400 limpio (sin filtrar nada).
        r = client.post(
            "/threads/foryou/",
            data="not-real-ciphertext",
            content_type="application/raw",
            HTTP_X_REQUEST_PAYLOAD="garbage",
            HTTP_CLIENT_ASSERTION=self.__token(),
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(ENCRYPTED_RESPONSE=False)
    def test_plain_body_ok_when_flag_off(self):
        # Modo plano: el middleware no exige cifrado (toggle real).
        before = Thread.objects.count()
        r = client.post(
            "/threads/",
            {"media": [], "text": "plano", "content": {"blocks": [], "entityMap": {}}},
            content_type="application/json",
            HTTP_CLIENT_ASSERTION=self.__token(),
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Thread.objects.count(), before + 1)

    def test_config_endpoint_contract(self):
        # /config/ devuelve los flags. El render sigue el flag (cifrado aquí,
        # ENCRYPTED_RESPONSE=True en el test env).
        from django.conf import settings

        r = client.get("/config/", HTTP_CLIENT_ASSERTION=self.__token())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        data = decode_body(r)
        self.assertEqual(data["encrypted_response"], settings.ENCRYPTED_RESPONSE)
        self.assertEqual(data["single_request_protect"], settings.SINGLE_REQUEST_PROTECT)
        # Metadata PÚBLICA: legible SIN ticket (AllowAny) → el bootstrap la
        # lee directa para conocer el flag antes de fijar el transporte.
        self.assertEqual(client.get("/config/").status_code, status.HTTP_200_OK)

    def test_honeypot_admin_exempt_from_encryption(self):
        # Honeypot (/admin/): HTML server-rendered, manda formularios en claro.
        # NO debe exigir body cifrado → el POST llega a la vista, no al 400 del
        # middleware ("Encrypted request body required").
        r = client.post(
            "/admin/login/", {"email": "scanner@evil.test", "password": "x"}, HTTP_CLIENT_ASSERTION=self.__token()
        )
        self.assertNotEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_real_admin_exempt_from_encryption(self):
        # Admin real (ruta ofuscada de settings.INTERNAL_ADMIN_URL): el POST de
        # login en claro llega a la vista de admin (no al 400 del middleware).
        from django.conf import settings

        r = client.post(
            f"/{settings.INTERNAL_ADMIN_URL}login/",
            {"username": "x", "password": "y"},
            HTTP_CLIENT_ASSERTION=self.__token(),
        )
        self.assertNotEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_not_exempted_by_admin_rule(self):
        # La exención es SOLO admin+honeypot: un endpoint de API con body en
        # claro sigue rechazado con 400 (la exención no se filtró a la API).
        r = client.post("/threads/foryou/", {}, content_type="application/json", HTTP_CLIENT_ASSERTION=self.__token())
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class SecretDerivationTest(SimpleTestCase):
    """API_SECRET_KEY is derived from SECRET_KEY; GATEWAY_SEED stays separate."""

    @staticmethod
    def _derive(secret_key: str) -> str:
        # Mirror of core/settings.py: HMAC-SHA256 with a fixed context label.
        import hashlib
        import hmac

        return hmac.new(secret_key.encode(), b"thiup-api-secret", hashlib.sha256).hexdigest()

    def test_api_secret_key_is_derived_from_secret_key(self):
        # The value the app signs tickets with is the derivation, not an env var.
        from django.conf import settings

        self.assertEqual(settings.API_SECRET_KEY, self._derive(settings.SECRET_KEY))

    def test_derivation_is_deterministic_and_key_dependent(self):
        # Same SECRET_KEY -> same key (issued tickets keep verifying across
        # restarts); a different SECRET_KEY yields a different key.
        self.assertEqual(self._derive("seed-A"), self._derive("seed-A"))
        self.assertNotEqual(self._derive("seed-A"), self._derive("seed-B"))

    def test_gateway_seed_is_independent_from_secret_key(self):
        # GATEWAY_SEED is PUBLIC obfuscation: it must NOT be derived from the
        # secret, so it never equals the derived key.
        from django.conf import settings

        self.assertNotEqual(settings.GATEWAY_SEED, settings.API_SECRET_KEY)
        self.assertNotEqual(settings.GATEWAY_SEED, self._derive(settings.SECRET_KEY))


class ThreadDetailsSerializationTest(TestCase):
    """The 'thread details' panel reads real fields from the thread payload."""

    def test_created_at_iso_present_and_matches(self):
        """created_at_iso is an absolute ISO timestamp; create_at stays relative."""
        mask = make_mask("author")
        thread = make_thread(mask, text="hello")

        data = ThreadSerializer(thread, context={"mask": mask}).data

        # New absolute timestamp for the details panel.
        self.assertIn("created_at_iso", data)
        self.assertEqual(data["created_at_iso"], thread.create_at.isoformat())
        # The card's compact relative string is untouched (contract preserved).
        self.assertIn("create_at", data)
        self.assertNotEqual(data["create_at"], data["created_at_iso"])

    def test_details_fields_available_without_extra_request(self):
        """uid, responses_count and reactions all travel in the same payload."""
        author = make_mask("author")
        replier = make_mask("replier")
        make_thread(replier, sub=make_thread(author))  # noise
        thread = make_thread(author, text="root")
        make_thread(replier, sub=thread, text="reply")

        data = ThreadSerializer(thread, context={"mask": author}).data

        self.assertEqual(data["uid"], thread.uid)  # public thread id
        self.assertEqual(data["responses_count"], 1)  # replies count
        self.assertIn("reactions", data)  # total derivable on FE


class IdentityFlagsTest(TestCase):
    """is_mine (feed/search) and is_op (thread replies) — boolean-only,
    anonymous, no cross-thread author correlation."""

    def setUp(self):
        self.author = make_mask("author")
        self.other = make_mask("other")

    def test_is_mine_true_for_own_thread_false_for_others(self):
        thread = make_thread(self.author, text="mine")
        # Viewed by its author → is_mine True.
        mine = ThreadSerializer(thread, context={"mask": self.author}).data
        self.assertTrue(mine["is_mine"])
        # Viewed by anyone else → is_mine False (private to the author).
        theirs = ThreadSerializer(thread, context={"mask": self.other}).data
        self.assertFalse(theirs["is_mine"])

    def test_is_op_false_without_op_mask_context(self):
        """Feed/search lists carry no op_mask → is_op is always False there."""
        thread = make_thread(self.author, text="root")
        data = ThreadSerializer(thread, context={"mask": self.author}).data
        self.assertFalse(data["is_op"])

    def test_is_op_marks_only_replies_by_the_thread_author(self):
        thread = make_thread(self.author, text="root")
        op_reply = make_thread(self.author, sub=thread, text="op reply")
        other_reply = make_thread(self.other, sub=thread, text="other reply")

        # Replies are serialized with op_mask = the thread author's mask.
        ctx = {"mask": self.other, "op_mask": thread.mask}
        op_data = ThreadSerializer(op_reply, context=ctx).data
        other_data = ThreadSerializer(other_reply, context=ctx).data

        self.assertTrue(op_data["is_op"])  # author's reply → OP
        self.assertFalse(other_data["is_op"])  # someone else's reply → not OP

    def test_flags_are_plain_booleans_no_author_identity_leaked(self):
        """The payload exposes booleans, never a reusable author id/hash field
        beyond the existing pseudonymous mask."""
        thread = make_thread(self.author, text="root")
        reply = make_thread(self.author, sub=thread, text="op reply")
        data = ThreadSerializer(reply, context={"mask": self.other, "op_mask": thread.mask}).data

        self.assertIsInstance(data["is_mine"], bool)
        self.assertIsInstance(data["is_op"], bool)
        # No op-author identity field is added to the payload.
        self.assertNotIn("op_mask", data)
        self.assertNotIn("author_id", data)


class OwnerStatsTest(TestCase):
    """Private /me stats: threads / reactions received / replies received.
    Owner-only, computed from the precomputed counters (one aggregate)."""

    def _set_counters(self, thread, reactors=0, commenters=0):
        Thread.objects.filter(pk=thread.pk).update(
            unique_reactors_count=reactors,
            unique_commenters_count=commenters,
        )

    def test_counts_threads_reactions_and_replies(self):
        from app.rest.masks import CurrentMaskView

        author = make_mask("author")
        # Two root threads + one reply BY the author (reply also counts toward
        # reactions/replies received on their content, but NOT toward "threads").
        t1 = make_thread(author, text="root 1")
        t2 = make_thread(author, text="root 2")
        reply = make_thread(author, sub=t1, text="author reply")
        self._set_counters(t1, reactors=3, commenters=2)
        self._set_counters(t2, reactors=1, commenters=0)
        self._set_counters(reply, reactors=4, commenters=1)

        stats = CurrentMaskView._owner_stats(author)

        self.assertEqual(stats["threads"], 2)  # only root threads
        self.assertEqual(stats["reactions"], 3 + 1 + 4)  # summed across all
        self.assertEqual(stats["replies"], 2 + 0 + 1)

    def test_stats_are_per_owner_not_global(self):
        from app.rest.masks import CurrentMaskView

        author = make_mask("author")
        other = make_mask("other")
        mine = make_thread(author, text="mine")
        self._set_counters(mine, reactors=5, commenters=2)
        theirs = make_thread(other, text="theirs")
        self._set_counters(theirs, reactors=9, commenters=7)

        # Each owner only sees their own totals — never the other's.
        self.assertEqual(CurrentMaskView._owner_stats(author), {"threads": 1, "reactions": 5, "replies": 2})
        self.assertEqual(CurrentMaskView._owner_stats(other), {"threads": 1, "reactions": 9, "replies": 7})

    def test_zero_when_no_threads(self):
        from app.rest.masks import CurrentMaskView

        empty = make_mask("empty")
        self.assertEqual(CurrentMaskView._owner_stats(empty), {"threads": 0, "reactions": 0, "replies": 0})


class SearchShadowbanTest(TestCase):
    """Blocklist shadowban: a blocked query answers exactly like a term
    nobody posted about, and soft-deletes the threads carrying the term."""

    def setUp(self):
        from django.core.cache import cache

        from app.models.blocked_term import BlockedTerm

        # Drop the blocked-terms LocMem cache left by other tests.
        cache.clear()
        self.author = make_mask("author")
        BlockedTerm.objects.create(term="palabraprohibida")

    def __get_client_token(self) -> str:
        return encode_token({"timestamp": datetime.now().__str__()})

    def search(self, q, search_type="posts"):
        r = client.get(
            "/search/",
            {"q": q, "type": search_type},
            HTTP_CLIENT_ASSERTION=self.__get_client_token(),
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        return decode_body(r)

    def test_blocked_query_returns_the_empty_no_match_shape(self):
        make_thread(self.author, text="difunde palabraprohibida aqui")
        body = self.search("palabraprohibida")
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["results"], [])
        self.assertEqual(body["counts"], {"posts": 0, "tags": 0, "users": 0, "media": 0})

    def test_blocked_query_shadowbans_whole_word_matches_only(self):
        hit = make_thread(self.author, text="difunde palabraprohibida aqui")
        # Contains the term only as a SUBSTRING of a longer word → innocent.
        inner = make_thread(self.author, text="palabraprohibidasufijo no es la palabra")
        clean = make_thread(self.author, text="texto normal")

        self.search("busco palabraprohibida ya")

        hit.refresh_from_db()
        inner.refresh_from_db()
        clean.refresh_from_db()
        self.assertFalse(hit.is_active)
        self.assertTrue(inner.is_active)
        self.assertTrue(clean.is_active)

    def test_seeded_terms_block_accent_and_case_insensitive(self):
        # 'pedofilia' is seeded by migration 0020; accents/case must not
        # bypass it (both sides normalize with strip_accents + lower).
        body = self.search("PEDOFILÍA")
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["counts"], {"posts": 0, "tags": 0, "users": 0, "media": 0})

    def test_clean_queries_are_unaffected(self):
        visible = make_thread(self.author, text="la lluvia de hoy")
        body = self.search("lluvia")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["uid"], visible.uid)

    def test_suggest_returns_nothing_for_blocked_query(self):
        make_thread(self.author, text="difunde palabraprohibida aqui")
        r = client.get(
            "/search/suggest/",
            {"q": "palabraprohibida"},
            HTTP_CLIENT_ASSERTION=self.__get_client_token(),
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(decode_body(r), {"tags": [], "threads": []})

    def test_threads_list_q_and_tag_are_blocked_too(self):
        thread = make_thread(self.author, text="difunde palabraprohibida")
        Tag.objects.create(thread=thread, name="palabraprohibida", name_norm="palabraprohibida")
        for params in ({"q": "palabraprohibida"}, {"tag": "palabraprohibida"}):
            r = client.get(
                "/threads/",
                params,
                HTTP_CLIENT_ASSERTION=self.__get_client_token(),
            )
            self.assertEqual(r.status_code, status.HTTP_200_OK)
            self.assertEqual(decode_body(r)["count"], 0)


class BlockedTermSweepTest(TestCase):
    """Adding a term takes effect immediately: the post_save signal sweeps
    the existing content and refreshes the cached blocklist."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.mask = make_mask("sweeper")

    def test_saving_a_term_sweeps_existing_threads_and_tags(self):
        from app.models.blocked_term import BlockedTerm

        pre = make_thread(self.mask, text="contenido con terminonuevo aqui")
        tagged = make_thread(self.mask, text="texto limpio")
        tag = Tag.objects.create(thread=tagged, name="terminonuevo", name_norm="terminonuevo")

        BlockedTerm.objects.create(term="Terminonuevo")

        pre.refresh_from_db()
        tag.refresh_from_db()
        tagged.refresh_from_db()
        self.assertFalse(pre.is_active)
        self.assertFalse(tag.is_active)
        # The tag falls, its thread stays: its own text is clean.
        self.assertTrue(tagged.is_active)

    def test_term_norm_is_derived_like_the_search_pipeline(self):
        from app.models.blocked_term import BlockedTerm

        term = BlockedTerm.objects.create(term="  Niños   Prohibidos ")
        self.assertEqual(term.term_norm, "ninos prohibidos")


class FeedTopReplyPreviewTest(TestCase):
    """X-style conversation preview: `top_reply` rides on a feed card ONLY
    when a direct reply earned it (>= FEED_TOP_REPLY_MIN_REACTORS unique
    reactors, the reply's own author never counting)."""

    def setUp(self):
        self.author = make_mask("author")
        self.user_b = make_mask("b")
        self.user_c = make_mask("c")
        # `love` is seeded by migration 0015; reuse it instead of recreating.
        self.reaction, _ = Reaction.objects.get_or_create(name="love", defaults={"emoji": "❤️"})

    def __get_client_token(self) -> str:
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def get_foryou(self):
        response = encrypted_post("/threads/foryou/", {}, self.__get_client_token())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return decode_body(response)

    def react(self, thread, mask):
        ReactionRelation.objects.create(thread=thread, mask=mask, reaction=self.reaction)

    def test_gate_and_winner_selection(self):
        root = make_thread(self.author, text="root")
        winner = make_thread(self.user_b, sub=root, text="winner")
        loser = make_thread(self.user_c, sub=root, text="loser")
        # winner: 2 unique reactors, none its own author → qualifies.
        self.react(winner, self.author)
        self.react(winner, self.user_c)
        # loser: a self-react (excluded) + 1 stranger → below the gate.
        self.react(loser, self.user_c)
        self.react(loser, self.author)
        # A root with no qualifying reply carries NO preview at all.
        bare = make_thread(self.author, text="bare")

        by_uid = {p["uid"]: p for p in self.get_foryou()["results"]}
        self.assertEqual(by_uid[root.uid]["top_reply"]["uid"], winner.uid)
        # The preview is a reply by user_b, not the root author → is_op False.
        self.assertFalse(by_uid[root.uid]["top_reply"]["is_op"])
        self.assertNotIn("top_reply", by_uid[bare.uid])

    def test_op_reply_preview_marks_is_op(self):
        root = make_thread(self.author, text="root")
        reply = make_thread(self.author, sub=root, text="op continues")
        self.react(reply, self.user_b)
        self.react(reply, self.user_c)

        card = next(p for p in self.get_foryou()["results"] if p["uid"] == root.uid)
        self.assertEqual(card["top_reply"]["uid"], reply.uid)
        self.assertTrue(card["top_reply"]["is_op"])

    def test_self_thread_rule_needs_no_reactions(self):
        """Rule A: the author continuing their own thread previews with ZERO
        engagement — the self-thread never competes."""
        root = make_thread(self.author, text="root")
        continuation = make_thread(self.author, sub=root, text="part two")

        card = next(p for p in self.get_foryou()["results"] if p["uid"] == root.uid)
        self.assertEqual(card["top_reply"]["uid"], continuation.uid)
        self.assertTrue(card["top_reply"]["is_op"])

    def test_relative_bar_scales_with_the_root(self):
        """Rule B's second gate: the SAME 2-reactor reply is notable on a
        modest thread but noise on a popular one (reactors >= RATIO × the
        root's precomputed unique_reactors_count)."""
        # Popular root (50 precomputed unique reactors): a 2-reactor reply
        # stays below 0.5 × 50 → NO preview.
        popular = make_thread(self.author, text="popular root")
        Thread.objects.filter(pk=popular.pk).update(unique_reactors_count=50)
        noise = make_thread(self.user_b, sub=popular, text="reply lost in the crowd")
        self.react(noise, self.author)
        self.react(noise, self.user_c)

        # Modest root (3 precomputed unique reactors): the same 2 reactors
        # clear 0.5 × 3 → preview.
        modest = make_thread(self.author, text="modest root")
        Thread.objects.filter(pk=modest.pk).update(unique_reactors_count=3)
        notable = make_thread(self.user_b, sub=modest, text="reply that carries the thread")
        self.react(notable, self.author)
        self.react(notable, self.user_c)

        by_uid = {p["uid"]: p for p in self.get_foryou()["results"]}
        self.assertNotIn("top_reply", by_uid[popular.uid])
        self.assertEqual(by_uid[modest.uid]["top_reply"]["uid"], notable.uid)

    def test_self_thread_beats_earned_reply_and_is_chronological(self):
        """Rule A wins over rule B, and among the author's own continuations
        the FIRST chronological one shows (X's thread order), not the most
        reacted one."""
        root = make_thread(self.author, text="root")
        # A third-party reply that EARNS rule B on its own...
        earned = make_thread(self.user_b, sub=root, text="popular stranger")
        self.react(earned, self.author)
        self.react(earned, self.user_c)
        # ...and two self-continuations, the SECOND one more reacted.
        first = make_thread(self.author, sub=root, text="part two", age_hours=0)
        Thread.objects.filter(pk=first.pk).update(create_at=timezone.now() - timedelta(hours=1))
        second = make_thread(self.author, sub=root, text="part three")
        self.react(second, self.user_b)
        self.react(second, self.user_c)

        card = next(p for p in self.get_foryou()["results"] if p["uid"] == root.uid)
        self.assertEqual(card["top_reply"]["uid"], first.uid)

    def test_preview_rides_on_every_feed_surface(self):
        """The shared helper serves /threads/ (list), /threads/mine/ and the
        search posts tab with the SAME two-story cards as the home feeds —
        the thread view (/responses/) never carries it (it shows the tree)."""
        root = make_thread(self.author, text="findme root")
        winner = make_thread(self.user_b, sub=root, text="the earned preview")
        self.react(winner, self.author)
        self.react(winner, self.user_c)

        token = self.__get_client_token()

        # /threads/ list (keyword filter).
        listed = decode_body(client.get("/threads/?q=findme", HTTP_CLIENT_ASSERTION=token))
        self.assertEqual(listed["results"][0]["top_reply"]["uid"], winner.uid)

        # Search posts tab (also the /m/:maskId thread list — same endpoint).
        # (/threads/mine/ shares the same helper; it can't be exercised here
        # because request.mask derives from the test client's IP, not from
        # make_mask's rows.)
        searched = decode_body(client.get("/search/?q=findme&type=posts", HTTP_CLIENT_ASSERTION=token))
        self.assertEqual(searched["results"][0]["top_reply"]["uid"], winner.uid)

        # The thread view keeps the real tree — no preview key on its head.
        responses = decode_body(client.get(f"/threads/{root.uid}/responses/", HTTP_CLIENT_ASSERTION=token))
        self.assertNotIn("top_reply", responses["head"])
