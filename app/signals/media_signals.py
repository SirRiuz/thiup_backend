# Python
import logging
from contextlib import contextmanager

# Django
from django.dispatch import receiver
from django.db.models.signals import post_delete

# Models
from app.models.media import ThreadFile

logger = logging.getLogger(__name__)

STORAGE_CLEANUP_UID = "threadfile_delete_storage_object"


@contextmanager
def storage_cleanup_paused():
    """Temporarily disconnect the per-row storage cleanup below.

    For bulk jobs (purge_inactive) that collect every doomed file_key UP FRONT
    and issue ONE batched DeleteObjects request — with the signal connected,
    deleting N rows would fire N individual storage calls, and Django could
    never fast-delete the queryset. Ad-hoc deletes (admin, API) keep the
    per-row signal.
    """
    post_delete.disconnect(
        sender=ThreadFile, dispatch_uid=STORAGE_CLEANUP_UID)
    try:
        yield
    finally:
        post_delete.connect(
            delete_storage_object,
            sender=ThreadFile,
            dispatch_uid=STORAGE_CLEANUP_UID,
        )


@receiver(post_delete, sender=ThreadFile, dispatch_uid=STORAGE_CLEANUP_UID)
def delete_storage_object(sender, instance, **kwargs):
    """When a ThreadFile row is DELETED, remove its binary from storage too.

    post_delete (not an overridden delete()) so every deletion path is covered:
    instance.delete(), queryset bulk deletes, and CASCADE from Thread/Mask.
    Goes through the StorageBackend adapter, so it works against whatever
    backend is configured (R2 today, anything S3-compatible or local tomorrow).

    Storage failures are logged, never raised: the row must go even if storage
    hiccups (an orphaned object costs cents; a resurrected DB row is a bug).
    NOTE: soft-delete (disable()) intentionally does NOT touch the binary —
    the row still references it.
    """
    if not instance.file_key:
        return
    # Local import: keeps signal import time free of storage/boto3 concerns.
    from app.methods.storage_backends import get_backend

    try:
        get_backend().delete_object(instance.file_key)
    except Exception:
        logger.warning(
            "ThreadFile %s deleted but its storage object could not be "
            "removed (key=%s) — clean it up manually.",
            instance.uid,
            instance.file_key,
        )
