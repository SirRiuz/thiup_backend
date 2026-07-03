# Python
import os
import uuid

# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


def thread_file_upload_to(instance, filename: str) -> str:
    """Kept for historical migrations only (the FileField was removed; the model
    now stores just a reference key — see `file_key`). Older migrations import
    this symbol, so it must remain importable."""
    ext = os.path.splitext(filename)[1].lower()
    return f"uploads/{uuid.uuid4().hex}{ext}"


class ThreadFile(BaseModel):
    """Media attached to a thread.

    The model holds ONLY a reference to the object in storage (R2/S3 key) — it
    never manages or uploads the binary. The file is uploaded directly from the
    client to storage via the presign/confirm flow; here we just record the key,
    the relation and a few flags. Everything else (dimensions, dominant color,
    compression + NSFW attributes) lives inside the `metadata` JSON.
    """

    is_video = models.BooleanField(default=False)

    # Nullable: a file is uploaded BEFORE the thread exists (the client uploads
    # as soon as compression + NSFW finish, before pressing Send). It stays
    # detached (thread=NULL, is_active=False) until `confirm` attaches it to the
    # thread created at Send.
    thread = models.ForeignKey(
        "app.Thread", on_delete=models.CASCADE, null=True, blank=True)

    # Uploader's pseudonymous mask, set at presign. Binds a pending (detached)
    # upload to its owner so `confirm` can only attach files the same mask
    # uploaded, to threads the same mask owns.
    mask = models.ForeignKey(
        "app.Mask", on_delete=models.CASCADE, null=True, blank=True)

    # NSFW flag from the CLIENT-SIDE detector (bypassable — informational only;
    # server-side moderation is a future step). Indexed so it can be used to
    # filter/hide media without a full scan.
    is_nsfw = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Marked NSFW by the client-side detector (informational).",
    )

    # CONFIGURATION metadata (NOT EXIF): the compression + NSFW attributes the
    # frontend computed and sent — including dimensions and dominant color, which
    # are NO LONGER dedicated columns. Stored verbatim after a light key
    # whitelist; the backend never opens the binary to extract anything.
    metadata = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text="Client-provided config metadata (compression + NSFW). Not EXIF.",
    )

    # Reference to the object in storage: a NON-guessable key whose name is a
    # long cryptographically-random token (NOT derived from id/uid), e.g.
    # 'uploads/<secrets.token_urlsafe>.webp'. Plain text — NOT a FileField. Kept
    # for storage operations (e.g. deleting the object).
    file_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Object key in storage (R2/S3 or local media). Reference only.",
    )

    # The full PUBLIC URL the client uses to display the file (e.g.
    # 'https://cdn.thiup.com/uploads/<token>.webp'). Persisted at confirm,
    # built from the same configurable base as the presign's public_url so they
    # always match. This is what the API exposes; file_key stays internal.
    file_url = models.CharField(
        max_length=600,
        blank=True,
        default="",
        help_text="Full public URL of the object (client-facing).",
    )

    def __str__(self) -> str:
        return self.uid
