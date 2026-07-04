# Python
from datetime import timedelta
from unittest import mock

# Django
from django.test import TestCase
from django.utils import timezone
from django.core.management import call_command

# Models
from app.models.mask import Mask
from app.models.thread import Thread
from app.models.media import ThreadFile
from app.models.purge_log import PurgeLog

# Helpers
from app.tests.test_thread_files import FakeBackend


def age(queryset, hours: int):
    """Backdate update_at via queryset update (bypasses auto_now)."""
    queryset.update(update_at=timezone.now() - timedelta(hours=hours))


class PurgeInactiveTest(TestCase):
    """Garbage collector: hard-deletes soft-deleted rows older than the age
    threshold, capped per run, cascades included, storage objects removed."""

    def setUp(self):
        self.backend = FakeBackend()
        patcher = mock.patch(
            "app.methods.storage_backends.get_backend",
            return_value=self.backend,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _inactive_file(self, key: str, hours_old: int = 48) -> ThreadFile:
        record = ThreadFile.objects.create(file_key=key, is_active=False)
        age(ThreadFile.objects.filter(pk=record.pk), hours_old)
        return record

    def test_purges_old_inactive_and_cleans_storage(self):
        self._inactive_file("m/aa/aa/stale.webp")
        call_command("purge_inactive")
        self.assertEqual(ThreadFile.objects.count(), 0)
        self.assertEqual(self.backend.deleted, ["m/aa/aa/stale.webp"])

    def test_fresh_inactive_rows_are_protected_by_min_age(self):
        # 1h-old pending upload: inactive by design, must survive the sweep.
        self._inactive_file("m/bb/bb/pending.webp", hours_old=1)
        call_command("purge_inactive")
        self.assertEqual(ThreadFile.objects.count(), 1)
        self.assertEqual(self.backend.deleted, [])

    def test_active_rows_are_never_touched(self):
        record = ThreadFile.objects.create(
            file_key="m/cc/cc/live.webp", is_active=True)
        age(ThreadFile.objects.filter(pk=record.pk), 48)
        call_command("purge_inactive")
        self.assertEqual(ThreadFile.objects.count(), 1)
        self.assertEqual(self.backend.deleted, [])

    def test_limit_caps_the_run_oldest_first(self):
        self._inactive_file("m/dd/dd/oldest.webp", hours_old=96)
        self._inactive_file("m/ee/ee/middle.webp", hours_old=72)
        self._inactive_file("m/ff/ff/newest.webp", hours_old=48)
        call_command("purge_inactive", limit=2)
        survivor = ThreadFile.objects.get()
        self.assertEqual(survivor.file_key, "m/ff/ff/newest.webp")
        self.assertEqual(
            sorted(self.backend.deleted),
            ["m/dd/dd/oldest.webp", "m/ee/ee/middle.webp"],
        )

    def test_thread_purge_cascades_to_children_and_storage(self):
        thread = Thread.objects.create(text="borrado", content={})
        Thread.objects.filter(pk=thread.pk).update(is_active=False)
        age(Thread.objects.filter(pk=thread.pk), 48)
        # Attached, ACTIVE file: dies with its thread (CASCADE), and its
        # storage object must go too.
        ThreadFile.objects.create(
            file_key="m/gg/gg/attached.webp", thread=thread, is_active=True)
        call_command("purge_inactive")
        self.assertEqual(Thread.objects.count(), 0)
        self.assertEqual(ThreadFile.objects.count(), 0)
        self.assertEqual(self.backend.deleted, ["m/gg/gg/attached.webp"])

    def test_reply_subtree_files_are_swept_with_the_root(self):
        root = Thread.objects.create(text="raiz", content={})
        Thread.objects.filter(pk=root.pk).update(is_active=False)
        age(Thread.objects.filter(pk=root.pk), 48)
        # ACTIVE reply with an ACTIVE file: both die by CASCADE with the root,
        # so the reply's storage object must be swept too.
        reply = Thread.objects.create(text="respuesta", content={}, sub=root)
        ThreadFile.objects.create(
            file_key="m/hh/hh/reply.webp", thread=reply, is_active=True)
        call_command("purge_inactive")
        self.assertEqual(Thread.objects.count(), 0)
        self.assertEqual(self.backend.deleted, ["m/hh/hh/reply.webp"])

    def test_many_files_use_one_batched_storage_request(self):
        for index in range(5):
            self._inactive_file(f"m/ii/ii/bulk{index}.webp")
        call_command("purge_inactive")
        self.assertEqual(len(self.backend.deleted), 5)
        # ONE DeleteObjects request for the whole run — never one per file.
        self.assertEqual(self.backend.batch_calls, 1)

    def test_db_query_count_is_constant_regardless_of_rows(self):
        for index in range(10):
            self._inactive_file(f"m/jj/jj/many{index}.webp")
        # One pk-scan per registry model (5) + the single fast DELETE for the
        # ThreadFile batch (pk+file_key ride the same scan) + the PurgeLog
        # insert = 7 queries total, independent of how many rows are purged.
        with self.assertNumQueries(7):
            call_command("purge_inactive")
        self.assertEqual(ThreadFile.objects.count(), 0)

    def test_excluded_models_survive_even_when_inactive(self):
        mask = Mask.objects.create(hash="f" * 64, is_active=False)
        age(Mask.objects.filter(pk=mask.pk), 48)
        thread = Thread.objects.create(text="del autor", content={}, mask=mask)
        call_command("purge_inactive")
        # The mask is NOT in the purge registry: it (and its content, which a
        # CASCADE would have taken) must survive.
        self.assertTrue(Mask.objects.filter(pk=mask.pk).exists())
        self.assertTrue(Thread.objects.filter(pk=thread.pk).exists())

    def test_every_run_writes_a_purge_log(self):
        self._inactive_file("m/kk/kk/logged.webp")
        call_command("purge_inactive", limit=500, min_age_hours=12)
        log = PurgeLog.objects.get()
        self.assertTrue(log.was_successful)
        self.assertEqual(log.row_limit, 500)
        self.assertEqual(log.min_age_hours, 12)
        self.assertEqual(log.selected_count, 1)
        self.assertEqual(log.deleted_count, 1)
        self.assertEqual(log.files_total, 1)
        self.assertEqual(log.files_removed, 1)
        self.assertEqual(
            log.breakdown, {"ThreadFile": {"selected": 1, "deleted": 1}})

    def test_failed_run_logs_the_error_and_reraises(self):
        self._inactive_file("m/ll/ll/doomed.webp")
        with mock.patch.object(
            self.backend, "delete_objects", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                call_command("purge_inactive")
        log = PurgeLog.objects.get()
        self.assertFalse(log.was_successful)
        self.assertIn("RuntimeError: boom", log.error)
        # The DB sweep had already happened when storage blew up.
        self.assertEqual(log.selected_count, 1)
