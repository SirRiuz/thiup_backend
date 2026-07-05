# Django
from django.urls import reverse
from django.core.cache import cache
from django.test import Client, TestCase
from django.contrib.auth.models import User

# Models
from app.models.mask import Mask
from app.models.thread import Thread
from app.models.momentum_log import MomentumLog

# Libs
from app.methods import presence
from app.methods.metrics import collect_metrics


class MetricsCollectorTest(TestCase):
    """collect_metrics(): one cheap on-demand snapshot for the owner."""

    def setUp(self):
        cache.clear()

    def test_snapshot_has_every_section(self):
        data = collect_metrics()
        for section in ("process", "system", "activity", "content",
                        "database", "jobs"):
            self.assertIn(section, data)
        # DB round-trip really ran and is a number.
        self.assertGreaterEqual(data["database"]["latency_ms"], 0)

    def test_online_now_counts_presence_marks(self):
        presence.mark_online("a" * 64)
        presence.mark_online("b" * 64)
        data = collect_metrics()
        self.assertEqual(data["activity"]["online_now"], 2)

    def test_content_counters_reflect_rows(self):
        Thread.objects.create(text="hola", content={})
        MomentumLog.objects.create(processed_count=7, updated_count=3)
        data = collect_metrics()
        self.assertEqual(data["content"]["threads"], 1)
        self.assertEqual(data["jobs"]["momentum"].processed_count, 7)
        self.assertIsNone(data["jobs"]["purge"])


class MetricsAdminViewTest(TestCase):
    """The dashboard lives behind the admin: staff-only, rendered template."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("admin:app_systemmetrics_changelist")

    def test_requires_staff(self):
        response = self.client.get(self.url)
        # Anonymous -> redirected to the admin login.
        self.assertEqual(response.status_code, 302)

    def test_renders_the_dashboard_for_the_owner(self):
        owner = User.objects.create_superuser("owner", "o@o.co", "x")
        presence.mark_online("c" * 64)
        self.client.force_login(owner)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/system_metrics.html")
        content = response.content.decode()
        self.assertIn("Online now", content)
        self.assertIn("App memory (RSS)", content)
        self.assertIn("Garbage collector", content)
        # The "Online now" card links to the online-filtered mask changelist.
        expected = reverse("admin:app_mask_changelist") + "?online=yes"
        self.assertIn(f'href="{expected}"', content)


class OnlineMaskChangelistTest(TestCase):
    """Mask changelist filtered by presence: only the users active now."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.force_login(
            User.objects.create_superuser("owner", "o@o.co", "x"))
        self.url = reverse("admin:app_mask_changelist")

    def test_online_filter_lists_only_active_masks(self):
        online = Mask.objects.create(hash="a1" * 32)
        Mask.objects.create(hash="b2" * 32)  # offline
        presence.mark_online(online.hash)

        response = self.client.get(self.url, {"online": "yes"})
        self.assertEqual(response.status_code, 200)
        # Listed: the marked mask AND the admin's own request mask (the
        # middleware marks every requester — including the owner browsing
        # the admin). The offline mask is filtered out.
        result_pks = {row.pk for row in response.context["cl"].result_list}
        self.assertIn(online.pk, result_pks)
        self.assertNotIn(
            Mask.objects.get(hash="b2" * 32).pk, result_pks)
        self.assertEqual(response.context["cl"].result_count, 2)

    def test_without_the_filter_everyone_is_listed(self):
        Mask.objects.create(hash="a1" * 32)
        Mask.objects.create(hash="b2" * 32)
        response = self.client.get(self.url)
        # 2 created + the superuser request's own middleware mask.
        self.assertEqual(response.context["cl"].result_count, 3)
