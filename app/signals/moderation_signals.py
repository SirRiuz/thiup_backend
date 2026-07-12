# Django
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

# Methods
from app.methods.moderation import invalidate_blocked_terms, shadowban_matching

# Models
from app.models.blocked_term import BlockedTerm

TERM_SAVED_UID = "blockedterm_saved_sweep"
TERM_DELETED_UID = "blockedterm_deleted_invalidate"


@receiver(post_save, sender=BlockedTerm, dispatch_uid=TERM_SAVED_UID)
def sweep_on_term_save(sender, instance, **kwargs):
    """Adding/enabling a blocked term takes effect IMMEDIATELY.

    The cached blocklist is dropped (next request reloads it) and existing
    content carrying the term is shadowbanned right away — without this,
    old threads would only be swept when someone happened to search the
    term. Deactivating a term only refreshes the cache: already-banned
    rows are never resurrected (the purge GC may have deleted them).
    """
    invalidate_blocked_terms()
    if instance.is_active and instance.term_norm:
        shadowban_matching([instance.term_norm])


@receiver(post_delete, sender=BlockedTerm, dispatch_uid=TERM_DELETED_UID)
def invalidate_on_term_delete(sender, instance, **kwargs):
    invalidate_blocked_terms()
