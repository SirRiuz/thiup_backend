# Django
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

# Constants
from app.constants.threads import THREAD_TEXT_MAX_LENGTH

# Models
from app.models.media import ThreadFile
from app.models.thread_edit import ThreadEditHistory

# Libs
from app.utils.time import format_short_time


class ThreadEditSerializer(serializers.Serializer):
    """Edits the text/media of a Thread (post or reply) the caller already
    owns — ownership itself is verified by the VIEW before this serializer
    ever runs (get_object_or_404(..., mask=request.mask), the real security
    boundary; this serializer only shapes/applies the change).

    `remove_media`/`add_media` uids are resolved against THIS thread AND the
    caller's own mask — anything that doesn't match (wrong owner, wrong
    thread, already gone) is silently dropped, never raised as an error, so
    a client racing itself (double-submit) can't fail a whole edit over a
    uid that's simply already applied.
    """

    text = serializers.CharField(
        required=True,
        max_length=THREAD_TEXT_MAX_LENGTH,
        trim_whitespace=False,
        help_text="New text of the thread.",
    )
    content = serializers.JSONField(required=True, help_text="New DraftJS content blob.")
    remove_media = serializers.ListField(
        child=serializers.CharField(max_length=12),
        required=False,
        default=list,
        help_text="ThreadFile uids to detach from this thread (hard-deleted).",
    )
    add_media = serializers.ListField(
        child=serializers.CharField(max_length=12),
        required=False,
        default=list,
        help_text="ThreadFile uids already confirmed against this thread to count as newly added.",
    )
    # Toggleable anytime, public↔private / on↔off repeatedly — unlike
    # is_snap, which never appears on this serializer at all (see
    # Thread.is_snap's docstring: immutable by construction, not by a
    # guard here). Omitted entirely → keeps the thread's current value
    # (see save() below); this is NOT a default=False field, so a client
    # that only wants to change the text never accidentally resets either.
    is_private = serializers.BooleanField(required=False)
    replies_disabled = serializers.BooleanField(required=False)

    def validate_text(self, value):
        # allow_blank=False already rejects "", but not whitespace-only —
        # trim_whitespace=False is deliberate at input (we don't silently
        # mutate what the client sent), so this check only gates emptiness.
        if not value.strip():
            raise serializers.ValidationError("Text cannot be empty.")
        return value

    def save(self):
        thread = self.instance
        mask = self.context["mask"]
        data = self.validated_data

        remove_qs = ThreadFile.objects.filter(
            uid__in=data.get("remove_media", []),
            thread=thread,
            mask=mask,
            is_active=True,
        )
        add_qs = ThreadFile.objects.filter(
            uid__in=data.get("add_media", []),
            thread=thread,
            mask=mask,
            is_active=True,
        )
        removed_count = remove_qs.count()
        added_count = add_qs.count()

        with transaction.atomic():
            # Snapshot what's about to be overwritten, BEFORE mutating.
            ThreadEditHistory.objects.create(
                thread=thread,
                previous_text=thread.text,
                media_added_count=added_count,
                media_removed_count=removed_count,
            )
            thread.text = data["text"]
            thread.content = data["content"]
            thread.edited_at = timezone.now()
            thread.is_private = data.get("is_private", thread.is_private)
            thread.replies_disabled = data.get("replies_disabled", thread.replies_disabled)
            thread.save()  # recomputes text_norm via Thread.save()
            # Hard-delete: fires the existing post_delete signal
            # (app/signals/media_signals.py) that cleans up storage — no new
            # cleanup code needed, same path as any other ThreadFile removal.
            remove_qs.delete()

        return thread


class ThreadEditHistorySerializer(serializers.ModelSerializer):
    """Public (not owner-gated), text-only revision — see ThreadEditHistory."""

    edited_at = serializers.SerializerMethodField(help_text="When this revision was superseded.")
    # Compact relative string ("2h", "3d"...), same helper/convention as
    # Thread.create_at on ThreadSerializer — so the history list reads with
    # the app's usual short-time idiom instead of a raw absolute timestamp.
    edited_at_short = serializers.SerializerMethodField()

    class Meta:
        model = ThreadEditHistory
        fields = ("uid", "previous_text", "media_added_count", "media_removed_count", "edited_at", "edited_at_short")

    def get_edited_at_short(self, obj) -> str:
        return format_short_time(obj.create_at)

    def get_edited_at(self, obj) -> str:
        return obj.create_at.isoformat()
