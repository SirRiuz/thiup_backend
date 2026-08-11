# Django
from rest_framework import serializers

# Models
from app.models.notification import Notification

# Serializers
from app.rest.serializers.mask_serializer import MaskSerializer
from app.rest.serializers.reaction_serializer import BaseReactionSerializer

# Libs
from app.utils.time import format_short_time


class NotificationThreadSerializer(serializers.Serializer):
    """Trimmed deep-link target — only what the FE needs to render/route,
    never the internal UUID (same thread-card-contract convention as
    ThreadSerializer)."""

    uid = serializers.CharField()
    text = serializers.SerializerMethodField()
    # Whether the DEEP-LINK TARGET is itself a reply (vs a root post) — lets
    # the FE pick "tu publicación" vs "tu comentario" wording without an
    # extra query: `sub_id` is always available on an already-loaded
    # instance, never triggers a fetch.
    is_reply = serializers.SerializerMethodField()

    def get_text(self, obj) -> str:
        return (obj.text or "")[:120]

    def get_is_reply(self, obj) -> bool:
        return obj.sub_id is not None


class NotificationGroupSerializer(serializers.Serializer):
    """Read-only, over the grouped dicts from
    app/methods/notifications.py::group_notifications — not a ModelSerializer,
    since its input isn't a single Notification instance."""

    # Null ONLY for `profile_view` groups (Notification.thread is nullable
    # for that one verb — its target is the actor, not a thread; see
    # app/models/notification.py). The FE branches on this: null → route to
    # the actor's own profile, otherwise → /p/<thread.uid>.
    thread = NotificationThreadSerializer(allow_null=True)
    verb = serializers.ChoiceField(choices=Notification.VERB_CHOICES)
    actors = MaskSerializer(many=True)
    actor_count = serializers.IntegerField()
    reaction = BaseReactionSerializer()
    is_read = serializers.BooleanField()
    create_at = serializers.SerializerMethodField()

    def get_create_at(self, obj) -> str:
        return format_short_time(obj["create_at"])


class DismissNotificationSerializer(serializers.Serializer):
    """Input for POST /notifications/dismiss/ — identifies a GROUPED row
    (thread_uid + verb), not a single Notification's internal pk, since
    grouping already collapses N rows into one displayed entry and
    dismissing it should remove all of them. Public identifiers only
    (thread's uid, not its internal UUID).

    `thread_uid` is omitted for `profile_view` (Notification.thread is null
    for that verb — there's nothing to key on besides the verb itself; see
    NotificationsViewSet.dismiss for how that's scoped to thread__isnull=True
    so it never touches thread-linked rows)."""

    thread_uid = serializers.CharField(max_length=12, required=False, allow_null=True, default=None)
    verb = serializers.ChoiceField(choices=Notification.VERB_CHOICES)

    def validate(self, attrs):
        is_thread_verb = attrs["verb"] in Notification.THREAD_VERBS
        if is_thread_verb and not attrs.get("thread_uid"):
            raise serializers.ValidationError("thread_uid is required for this verb.")
        if not is_thread_verb and attrs.get("thread_uid"):
            raise serializers.ValidationError("thread_uid must be omitted for this verb.")
        return attrs
