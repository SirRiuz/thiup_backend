# Python
import hashlib
from io import StringIO
from types import SimpleNamespace
from unittest import mock

# Django
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings

# Libs
from app.methods import metrics, presence
from app.models.mask import Mask
from app.models.media import ThreadFile
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation
from app.models.tag import Tag

# Models
from app.models.thread import Thread
from app.rest.masks import CurrentMaskView
from app.rest.serializers.media_serializer import ThreadMediaSerializer
from app.rest.serializers.reaction_serializer import ReactionSerializer
from core.settings import env_list, env_required

client = Client()

# The mask MaskMiddleware derives for the Django test client (127.0.0.1).
REQUESTER_HASH = hashlib.sha256(b"127.0.0.1").hexdigest()


class LoadFixturesCommandTest(TestCase):
    def test_loads_then_reports(self):
        # Start from an empty catalog so the fixture actually loads.
        ReactionRelation.objects.all().delete()
        Reaction.objects.all().delete()
        out = StringIO()
        call_command("load_fixtures", stdout=out)
        self.assertIn("Fixtures ready", out.getvalue())
        self.assertTrue(Reaction.objects.exists())

    def test_already_present_is_skipped(self):
        out = StringIO()
        target = "app.management.commands.load_fixtures.management.call_command"
        with mock.patch(target, side_effect=Exception("duplicate key value violates unique constraint")):
            call_command("load_fixtures", stdout=out)
        self.assertIn("already present", out.getvalue())

    def test_unexpected_error_is_reported(self):
        out = StringIO()
        target = "app.management.commands.load_fixtures.management.call_command"
        with mock.patch(target, side_effect=Exception("boom")):
            call_command("load_fixtures", stdout=out)
        self.assertIn("Error loading", out.getvalue())


class MediaSerializerFallbackTest(TestCase):
    def setUp(self):
        self.mask = Mask.objects.create(hash="a" * 64, country_code="CO")
        self.thread = Thread.objects.create(mask=self.mask, text="hola", content={})

    def test_file_derived_from_key_for_legacy_rows(self):
        # Legacy rows saved before file_url existed: the URL is derived from
        # the stored key via the storage adapter.
        legacy = ThreadFile.objects.create(thread=self.thread, mask=self.mask, file_url="", file_key="m/aa/x.webp")
        backend = mock.Mock()
        backend.public_url.side_effect = lambda key, base_url=None: f"https://cdn/{key}"
        with mock.patch("app.rest.serializers.media_serializer.get_backend", return_value=backend):
            data = ThreadMediaSerializer(legacy).data
        self.assertEqual(data["file"], "https://cdn/m/aa/x.webp")

    def test_file_none_without_url_or_key(self):
        empty = ThreadFile.objects.create(thread=self.thread, mask=self.mask, file_url="", file_key="")
        self.assertIsNone(ThreadMediaSerializer(empty).data["file"])

    def test_dimensions_coerced_defensively(self):
        junk = ThreadFile.objects.create(
            thread=self.thread,
            mask=self.mask,
            file_url="https://cdn/x.webp",
            metadata={"width": "junk", "height": None},
        )
        data = ThreadMediaSerializer(junk).data
        self.assertEqual(data["width"], 0)
        self.assertEqual(data["height"], 0)


class ReactionSerializerLegacyPathTest(TestCase):
    def test_thread_context_counts_per_reaction(self):
        # The slow-path fallback (serializer called with a thread in context)
        # must keep reporting the per-reaction count.
        mask = Mask.objects.create(hash="b" * 64, country_code="CO")
        thread = Thread.objects.create(mask=mask, text="hola", content={})
        reaction = Reaction.objects.create(name="love-test", emoji="❤️")
        ReactionRelation.objects.create(mask=mask, thread=thread, reaction=reaction)

        data = ReactionSerializer(reaction, context={"thread": thread}).data
        self.assertEqual(data["reaction_count"], 1)


class PresenceIntrospectionTest(TestCase):
    def test_online_listing_degrades_without_locmem(self):
        # Any backend without LocMem's private _expire_info reports
        # "unavailable" (None) instead of breaking the admin dashboard.
        with mock.patch.object(presence, "cache", SimpleNamespace()):
            self.assertIsNone(presence.online_hashes())
            self.assertIsNone(presence.online_count())


class MetricsHelpersTest(TestCase):
    def test_proc_kb_missing_file(self):
        self.assertIsNone(metrics._proc_kb("/nonexistent/proc/file", "MemTotal"))

    def test_system_memory_none_when_unreadable(self):
        with mock.patch.object(metrics, "_proc_kb", return_value=None):
            self.assertEqual(metrics._system_memory(), (None, None, None))

    def test_uptime_human_variants(self):
        self.assertEqual(metrics._uptime_human(2 * 86400 + 3600), "2d 1h")
        self.assertEqual(metrics._uptime_human(3 * 3600 + 120), "3h 2m")
        self.assertEqual(metrics._uptime_human(59), "0m 59s")

    def test_collect_metrics_snapshot_keys(self):
        snapshot = metrics.collect_metrics()
        for key in ("generated_at", "process", "content", "database"):
            self.assertIn(key, snapshot)


class SettingsHelpersTest(TestCase):
    def test_env_required_raises_when_missing(self):
        with self.assertRaises(ImproperlyConfigured):
            env_required("DEFINITELY_MISSING_ENV_VAR", description="test")

    def test_env_list_strips_and_drops_empties(self):
        self.assertEqual(env_list("DEFINITELY_MISSING_ENV_VAR", default="a, b,,"), ["a", "b"])


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class CurrentMaskViewGuardTest(TestCase):
    def test_404_without_mask(self):
        # Defensive branch: a request that somehow skipped MaskMiddleware.
        request = RequestFactory().get("/me/")
        request.mask = None
        response = CurrentMaskView.as_view()(request)
        self.assertEqual(response.status_code, 404)


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadsListEdgesTest(TestCase):
    def setUp(self):
        self.own_mask, _ = Mask.objects.get_or_create(hash=REQUESTER_HASH)
        self.other_mask = Mask.objects.create(hash="d" * 64, country_code="CO")
        self.own = Thread.objects.create(mask=self.own_mask, text="mine", content={})
        self.other = Thread.objects.create(mask=self.other_mask, text="theirs", content={})

    def test_ordering_by_reactions_count(self):
        r = client.get("/threads/?ordering=-reactions_count")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 2)

    def test_query_and_tag_length_guard(self):
        too_long = "x" * 101
        self.assertEqual(client.get(f"/threads/?q={too_long}").status_code, 400)
        self.assertEqual(client.get(f"/threads/?tag={too_long}").status_code, 400)

    def test_mine_is_scoped_to_the_requester(self):
        r = client.get("/threads/mine/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([t["uid"] for t in r.data["results"]], [self.own.uid])


@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class SearchEdgesTest(TestCase):
    def setUp(self):
        self.mask = Mask.objects.create(hash="e" * 64, country_code="CO")
        self.thread = Thread.objects.create(
            mask=self.mask,
            text="palabra " * 30 + "lluvia" + " palabra" * 30,
            content={},
        )
        Tag.objects.create(thread=self.thread, name="lluvia", name_norm="lluvia")

    def test_invalid_type_falls_back_to_posts(self):
        r = client.get("/search/?q=lluvia&type=bogus")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["counts"]["posts"], 1)

    def test_author_query_with_no_matching_mask(self):
        r = client.get("/search/?q=@abc123&type=posts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["results"], [])

    def test_tags_activity_is_cached_between_requests(self):
        first = client.get("/search/?q=lluvia&type=tags")
        second = client.get("/search/?q=lluvia&type=tags")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.data["results"][0]["activity"],
            second.data["results"][0]["activity"],
        )

    def test_suggest_snippet_has_context_ellipses(self):
        r = client.get("/search/suggest/?q=lluvia")
        self.assertEqual(r.status_code, 200)
        snippets = [t["snippet"] for t in r.data["threads"]]
        self.assertTrue(snippets and all("lluvia" in s for s in snippets))
        # The match sits in the middle of a long text → both ellipses.
        self.assertTrue(snippets[0].startswith("…"))
        self.assertTrue(snippets[0].endswith("…"))


class TailBranchesTest(TestCase):
    """Small defensive branches left uncovered by the functional suites."""

    def test_token_matches_rejects_non_string_inputs(self):
        from app.methods.gateway_path import token_matches

        self.assertFalse(token_matches(None, "nonce"))
        self.assertFalse(token_matches("token", None))
        self.assertFalse(token_matches("token", ""))

    def test_proc_kb_field_absent(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt") as handle:
            handle.write("OtherField:   12 kB\n")
            handle.flush()
            self.assertIsNone(metrics._proc_kb(handle.name, "MemTotal"))

    def test_db_size_none_off_postgres(self):
        with mock.patch.object(metrics, "connection") as conn:
            conn.vendor = "sqlite"
            self.assertIsNone(metrics._db_size())

    def test_collect_metrics_survives_missing_loadavg(self):
        with mock.patch("app.methods.metrics.os.getloadavg", side_effect=OSError):
            snapshot = metrics.collect_metrics()
        self.assertIn("process", snapshot)

    def test_legacy_upload_to_helper(self):
        # Kept only because historical migrations import it.
        from app.models.media import thread_file_upload_to

        path = thread_file_upload_to(None, "photo.JPG")
        self.assertTrue(path.startswith("uploads/"))
        self.assertTrue(path.endswith(".jpg"))

    def test_local_upload_invalid_content_length_header(self):
        from app.methods.storage_backends import local_upload_put

        request = RequestFactory().put("/x", data=b"abc", content_type="image/webp")
        request.META["CONTENT_LENGTH"] = "not-a-number"
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            response = local_upload_put(request, "m/aa/x.webp")
        self.assertEqual(response.status_code, 200)

    def test_suggest_query_length_guard(self):
        with override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False):
            r = client.get(f"/search/suggest/?q={'x' * 101}")
        self.assertEqual(r.status_code, 400)

    def test_snippet_without_match_truncates(self):
        from app.rest.search import SearchViewSet

        view = SearchViewSet()
        snippet = view._SearchViewSet__snippet("short text without the term", "zzz")
        self.assertTrue(snippet.startswith("short text"))

    def test_momentum_flushes_mid_loop_batches(self):
        from django.core.management import call_command as run

        author = Mask.objects.create(hash="f" * 64, country_code="CO")
        reactor = Mask.objects.create(hash="9" * 64, country_code="CO")
        reaction = Reaction.objects.create(name="tail-love", emoji="❤️")
        for i in range(2):
            thread = Thread.objects.create(mask=author, text=f"hot {i}", content={})
            ReactionRelation.objects.create(mask=reactor, thread=thread, reaction=reaction)
        run("recompute_momentum", batch_size=1, stdout=StringIO())
        self.assertTrue(Thread.objects.filter(momentum_score__gt=0).exists())

    def test_exempt_prefix_skips_request_crypto(self):
        # POST to an exempt prefix passes the middleware untouched even with
        # the encrypted transport on (405 comes from the view, not a 400).
        response = client.post("/health/", {"x": 1}, content_type="application/json")
        self.assertEqual(response.status_code, 405)


class AdminDisplayHelpersTest(TestCase):
    """The tiny list_display/readonly helpers of the owner admin."""

    def setUp(self):
        self.mask = Mask.objects.create(hash="c" * 64, country_code="CO")
        self.thread = Thread.objects.create(mask=self.mask, text="hola", content={})

    def _admin_for(self, model):
        from django.contrib import admin as dj_admin

        return dj_admin.site._registry[model]

    def test_base_readonly_fields_are_appended(self):
        thread_admin = self._admin_for(Thread)
        readonly = thread_admin.get_readonly_fields(None)
        self.assertIn("create_at", readonly)
        self.assertIn("update_at", readonly)

    def test_thread_attach_files_counts_active_media(self):
        ThreadFile.objects.create(thread=self.thread, mask=self.mask, file_url="u", file_key="k1")
        ThreadFile.objects.create(thread=self.thread, mask=self.mask, file_url="u", file_key="k2", is_active=False)
        self.assertEqual(self._admin_for(Thread).attach_files(self.thread), 1)

    def test_thread_file_extension_and_link(self):
        admin_cls = self._admin_for(ThreadFile)
        with_ext = ThreadFile.objects.create(
            thread=self.thread, mask=self.mask, file_key="m/aa/x.webp", file_url="https://cdn/x.webp"
        )
        self.assertEqual(admin_cls.extension(with_ext), "webp")
        self.assertIn("https://cdn/x.webp", admin_cls.file_link(with_ext))
        bare = ThreadFile.objects.create(thread=self.thread, mask=self.mask, file_key="noext", file_url="")
        self.assertNotEqual(admin_cls.extension(bare), "")
        self.assertEqual(admin_cls.file_link(bare), "—")

    def test_reaction_relation_preview(self):
        reaction = Reaction.objects.create(name="admin-love", emoji="❤️")
        relation = ReactionRelation.objects.create(mask=self.mask, thread=self.thread, reaction=reaction)
        self.assertEqual(self._admin_for(ReactionRelation).reaction_prev(relation), "❤️")

    def test_report_short_reason_truncates(self):
        from app.models.report import Report

        report = Report.objects.create(thread=self.thread, reporter=self.mask, reason="x" * 100)
        short = self._admin_for(Report).short_reason(report)
        self.assertEqual(len(short), 61)
        self.assertTrue(short.endswith("…"))


class SettingsAwsBlockTest(TestCase):
    """Execute the USE_AWS_STORAGE branch of settings by reloading the module
    with the AWS env present. Only MODULE attributes are affected (the live
    django.conf.settings object was materialized at boot), and the module is
    reloaded back with the real env afterwards."""

    def test_aws_block_configures_s3_storages(self):
        import importlib
        import os as os_module

        import core.settings as settings_module

        env = {
            "USE_AWS_STORAGE": "True",
            "AWS_STORAGE_BUCKET_NAME": "bucket-test",
            "AWS_S3_CUSTOM_DOMAIN": "cdn.test",
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        try:
            with mock.patch.dict(os_module.environ, env):
                reloaded = importlib.reload(settings_module)
                self.assertEqual(reloaded.AWS_STORAGE_BUCKET_NAME, "bucket-test")
                self.assertEqual(reloaded.AWS_S3_CUSTOM_DOMAIN, "cdn.test")
                # Under pytest the storage is force-overridden to in-memory
                # AFTER the AWS block — the suite never touches a bucket.
                self.assertEqual(
                    reloaded.STORAGES["default"]["BACKEND"],
                    "django.core.files.storage.InMemoryStorage",
                )
        finally:
            importlib.reload(settings_module)


class FinalBranchesTest(TestCase):
    def setUp(self):
        self.mask = Mask.objects.create(hash="ab" * 32, country_code="CO")
        self.thread = Thread.objects.create(mask=self.mask, text="hola bloqueame", content={})

    def test_blocked_term_save_sweeps_matching_content(self):
        from app.models.blocked_term import BlockedTerm

        term = BlockedTerm.objects.create(term="bloqueame")
        self.thread.refresh_from_db()
        self.assertFalse(self.thread.is_active)
        # Deleting the term only refreshes the cache (never resurrects rows).
        term.delete()
        self.thread.refresh_from_db()
        self.assertFalse(self.thread.is_active)

    @override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
    def test_inactive_reaction_skipped_in_fast_path(self):
        reaction = Reaction.objects.create(name="ghost", emoji="👻", is_active=False)
        ReactionRelation.objects.create(mask=self.mask, thread=self.thread, reaction=reaction)
        r = client.get("/threads/")
        self.assertEqual(r.data["results"][0]["reactions"], [])

    def test_clean_metadata_rejects_non_dict(self):
        from app.rest.serializers.thread_file_serializer import clean_metadata

        self.assertEqual(clean_metadata("junk"), {})
        self.assertEqual(clean_metadata(None), {})

    @override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
    def test_confirm_rejects_content_type_mismatch(self):
        pending = ThreadFile.objects.create(
            uid="mismatch1234",
            file_key="m/aa/y.webp",
            mask=Mask.objects.get_or_create(hash=REQUESTER_HASH)[0],
            thread=None,
            is_video=False,
            is_active=False,
        )
        own_thread = Thread.objects.create(mask=Mask.objects.get(hash=REQUESTER_HASH), text="mine", content={})
        backend = mock.Mock()
        backend.object_exists.return_value = {"content_length": 10, "content_type": "video/mp4"}
        with mock.patch("app.rest.thread_files.get_backend", return_value=backend):
            r = client.post(
                "/thread-files/confirm/",
                {
                    "uid": pending.uid,
                    "thread": own_thread.uid,
                    "is_video": False,
                    "width": 10,
                    "height": 10,
                    "target_color": "#000000",
                    "is_nsfw": False,
                    "metadata": {},
                },
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 400)

    @override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
    def test_mine_requires_a_mask(self):
        from app.rest.threads import ThreadsViewSet

        request = RequestFactory().get("/threads/mine/")
        request.mask = None
        response = ThreadsViewSet.as_view({"get": "mine"})(request)
        self.assertEqual(response.status_code, 404)


class SwaggerRouteTest(TestCase):
    def test_swagger_lives_under_the_real_admin_path(self):
        from django.conf import settings as dj_settings
        from django.urls import NoReverseMatch, reverse

        if not dj_settings.ENABLE_SWAGGER:
            with self.assertRaises(NoReverseMatch):
                reverse("schema-swagger-ui")
            return
        url = reverse("schema-swagger-ui")
        self.assertEqual(url, f"/{dj_settings.INTERNAL_ADMIN_URL}swagger/")
        # Never under the honeypot decoy: /admin/swagger/ falls into the fake
        # admin (redirect to its login), not the schema page.
        response = client.get("/admin/swagger/")
        self.assertEqual(response.status_code, 302)


class LocationLookupTest(TestCase):
    def test_unknown_address_falls_back(self):
        from app.methods.location import get_country

        # Private/loopback addresses are not in the GeoLite DB.
        self.assertEqual(get_country("127.0.0.1"), "Unknow")

    def test_public_address_resolves_iso_code(self):
        from app.methods.location import get_country

        country = get_country("8.8.8.8")
        # Deterministic in practice (Google DNS → US) but assert loosely:
        # a 2-letter ISO code, never the fallback.
        self.assertIsInstance(country, str)
        self.assertEqual(len(country), 2)
