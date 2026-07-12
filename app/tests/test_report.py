import json

from django.contrib import admin as dj_admin
from django.test import Client, TestCase, override_settings
from rest_framework import status

from app.admin import ReportAdmin
from app.models.mask import Mask
from app.models.report import Report
from app.models.thread import Thread


def make_thread():
    author = Mask.objects.create(hash="hash-author", country_code="CO")
    return Thread.objects.create(content={}, text="reported", mask=author)


# Flags off: IsClientAuthenticated is a no-op and bodies are plain JSON. The
# reporter mask is derived by MaskMiddleware from the (stable) test client IP,
# so two posts from the same client share one mask → upsert.
@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ReportEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.thread = make_thread()

    def _post(self, body):
        return self.client.post("/reports/", data=json.dumps(body), content_type="application/json")

    def test_create_report(self):
        res = self._post({"thread_id": self.thread.uid, "category": "spam_or_deception"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Report.objects.count(), 1)
        report = Report.objects.get()
        self.assertEqual(report.thread, self.thread)
        self.assertEqual(report.category, "spam_or_deception")
        self.assertFalse(report.is_priority)
        # Reporter derived server-side (never sent by the client).
        self.assertIsNotNone(report.reporter)

    def test_rereport_same_thread_updates_not_duplicates(self):
        self._post({"thread_id": self.thread.uid, "category": "spam_or_deception"})
        res = self._post({"thread_id": self.thread.uid, "category": "harassment", "reason": "changed my mind"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(Report.objects.count(), 1)  # no duplicate
        report = Report.objects.get()
        self.assertEqual(report.category, "harassment")
        self.assertEqual(report.reason, "changed my mind")

    def test_minors_marks_priority(self):
        self._post({"thread_id": self.thread.uid, "category": "minors"})
        self.assertTrue(Report.objects.get().is_priority)

    def test_invalid_category_is_rejected(self):
        res = self._post({"thread_id": self.thread.uid, "category": "nope"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Report.objects.count(), 0)

    def test_reason_too_long_is_rejected(self):
        res = self._post({"thread_id": self.thread.uid, "category": "other", "reason": "x" * 301})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_thread_is_404(self):
        res = self._post({"thread_id": "doesnotexist", "category": "spam_or_deception"})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class ReportAdminPermsTest(TestCase):
    """Reports can be viewed and deleted, never added or edited in the admin."""

    def test_add_and_change_disabled_delete_and_view_default(self):
        report_admin = ReportAdmin(Report, dj_admin.site)
        self.assertFalse(report_admin.has_add_permission(None))
        self.assertFalse(report_admin.has_change_permission(None))
        # Add/change are explicitly overridden; delete/view keep the defaults.
        self.assertIn("has_add_permission", ReportAdmin.__dict__)
        self.assertIn("has_change_permission", ReportAdmin.__dict__)
        self.assertNotIn("has_delete_permission", ReportAdmin.__dict__)
        # Reporter is never exposed in the admin (anonymity).
        self.assertIn("reporter", ReportAdmin.exclude)
