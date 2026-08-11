# Django
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

# Libs
from app.methods.notifications import group_notifications

# Models
from app.middlewares.mask import invalidate_mask_cache
from app.models.mask import Mask
from app.models.notification import Notification
from app.permissions.client import IsClientAuthenticated
from app.permissions.throttling import TrustedIPScopedRateThrottle
from app.rest.serializers.notification_serializer import (
    DismissNotificationSerializer,
    NotificationGroupSerializer,
)


class NotificationsViewSet(GenericViewSet):
    """
    GET  /notifications/               → paginated inbox, grouped at read
    GET  /notifications/unread-count/  → {"unread_count": N}, zero extra queries
    POST /notifications/mark-read/     → flips every unread row + resets the badge
    POST /notifications/dismiss/       → deletes a whole grouped entry

    Read-only or non-content-creating: no @human_validator on any action
    (same convention as foryou/closeyou). Reuses the existing "profile"
    throttle scope (120/min) — no new scope needed for reads.
    """

    serializer_class = NotificationGroupSerializer
    permission_classes = (IsClientAuthenticated,)
    throttle_classes = (TrustedIPScopedRateThrottle,)
    throttle_scope = "profile"

    def get_queryset(self):
        return (
            Notification.objects.filter(recipient=self.request.mask)
            .select_related("actor", "thread", "reaction")
            .order_by("-create_at")
        )

    def list(self, request) -> Response:
        # Hand-rolled instead of ListModelMixin: grouping sits between
        # pagination and serialization.
        page = self.paginate_queryset(self.get_queryset())
        grouped = group_notifications(page)
        serializer = self.get_serializer(grouped, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False, methods=["GET"], url_path="unread-count")
    def unread_count(self, request) -> Response:
        return Response({"unread_count": request.mask.unread_notifications_count})

    @action(detail=False, methods=["POST"], url_path="mark-read")
    def mark_read(self, request) -> Response:
        Notification.objects.filter(recipient=request.mask, is_read=False).update(is_read=True)
        Mask.objects.filter(pk=request.mask.pk).update(unread_notifications_count=0)
        # Zero extra query: request.mask.hash is already the middleware's
        # own instance. Without this, the cached Mask (see MaskMiddleware)
        # keeps serving the PRE-reset count for up to 60s.
        invalidate_mask_cache(request.mask.hash)
        return Response({"status": "ok"})

    @action(detail=False, methods=["POST"], url_path="dismiss")
    def dismiss(self, request) -> Response:
        # Identifies the GROUPED row (thread_uid + verb), not a single
        # Notification's internal pk — grouping already collapsed however
        # many rows into one displayed entry, so dismissing it removes all
        # of them. No counter/cache bookkeeping needed here: by the time
        # this endpoint is reachable (the list has to have loaded first),
        # mark-read already ran on mount and zeroed the badge — there's
        # never anything unread left to account for on dismiss.
        serializer = DismissNotificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        thread_uid = serializer.validated_data["thread_uid"]
        filters = {"recipient": request.mask, "verb": serializer.validated_data["verb"]}
        # profile_view rows carry no thread (see Notification.thread) —
        # scoped by thread__isnull instead, so this never touches a
        # thread-linked row by accident.
        if thread_uid:
            filters["thread__uid"] = thread_uid
        else:
            filters["thread__isnull"] = True
        deleted, _ = Notification.objects.filter(**filters).delete()
        return Response({"status": "ok", "deleted": deleted})
