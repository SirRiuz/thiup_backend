# Django
from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver

# Libs
from app.middlewares.mask import invalidate_mask_cache

# Models
from app.models.mask import Mask
from app.models.notification import Notification
from app.models.reaction_relation import ReactionRelation
from app.models.thread import Thread

REACTION_NOTIFY_UID = "notification_on_reaction"
REPLY_NOTIFY_UID = "notification_on_reply"
BADGE_BUMP_UID = "notification_bump_unread_badge"


def _notify(recipient_id, actor_id, **notification_kwargs) -> None:
    """Create the Notification row. Skips self-notifications (a mask
    interacting with its own content never notifies itself).

    The badge bump + cache invalidation is NOT done here — it lives on
    Notification's own post_save (bump_unread_badge below), so it fires
    identically regardless of WHO creates the row: this signal path, the
    Django admin, or any future code that calls Notification.objects.create()
    directly.
    """
    if recipient_id is None or recipient_id == actor_id:
        return
    Notification.objects.create(recipient_id=recipient_id, actor_id=actor_id, **notification_kwargs)


@receiver(post_save, sender=Notification, dispatch_uid=BADGE_BUMP_UID)
def bump_unread_badge(sender, instance, created, **kwargs):
    """Single source of truth for "a Notification now exists": atomically
    bump the recipient's badge, no read-before-write, then invalidate their
    cached Mask (MaskMiddleware caches the whole instance for 60s on the
    assumption it's immutable outside country_code — this write usually
    happens on the ACTOR's request, not the recipient's, so the recipient's
    already-cached instance must be dropped or it keeps serving the
    pre-increment count for up to 60s). Runs for admin-created rows too —
    that's the point: creating one by hand behaves exactly like an organic
    one.
    """
    if not created:
        return
    Mask.objects.filter(pk=instance.recipient_id).update(unread_notifications_count=F("unread_notifications_count") + 1)
    recipient_hash = Mask.objects.filter(pk=instance.recipient_id).values_list("hash", flat=True).first()
    if recipient_hash:
        invalidate_mask_cache(recipient_hash)


@receiver(post_save, sender=ReactionRelation, dispatch_uid=REACTION_NOTIFY_UID)
def notify_on_reaction(sender, instance, created, **kwargs):
    """Notify the reacted-to thread's author.

    `ReactionRelationSerializer.create()` (app/rest/serializers/
    reaction_serializer.py) returns None without ever calling
    `.create()` on a toggle-OFF (same emoji again) — no `post_save`
    fires then, so no extra guard is needed here. Switching to a
    DIFFERENT emoji deletes-then-creates, so it DOES notify again —
    intentional (see app/tests/test_notifications.py).

    `instance.thread` is already a cached Python instance (resolved by the
    serializer's SlugRelatedField before `.create()`), so `.mask_id` is a
    zero-query attribute access.
    """
    if not created:
        return
    _notify(
        recipient_id=instance.thread.mask_id,
        actor_id=instance.mask_id,
        verb=Notification.REACTION,
        thread=instance.thread,
        reaction_id=instance.reaction_id,
    )


@receiver(post_save, sender=Thread, dispatch_uid=REPLY_NOTIFY_UID)
def notify_on_reply(sender, instance, created, **kwargs):
    """Notify the parent thread's author when a reply is created.

    `instance.sub` is already a cached Python instance (resolved by
    ThreadSerializer's SlugRelatedField before `.create()`), so `.mask_id`
    is a zero-query attribute access.
    """
    if not created or instance.sub_id is None:
        return
    _notify(
        recipient_id=instance.sub.mask_id,
        actor_id=instance.mask_id,
        verb=Notification.REPLY,
        thread=instance,
    )
