# Django
from rest_framework import serializers

from app.constants.engagement import BATCH_MAX_EVENT_COUNT, BATCH_MAX_EVENTS
from app.models.engagement_daily import EngagementDaily

EVENT_TYPE_CHOICES = ("view", "qr", "link_copy", "download", "share")


class BatchEventSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=EngagementDaily.TARGET_TYPE_CHOICES)
    target_uid = serializers.CharField(max_length=12)
    type = serializers.ChoiceField(choices=EVENT_TYPE_CHOICES)
    # Pre-aggregated by the client (see engagementQueue.js) — one entry per
    # (target, type) already carries the whole session's count.
    count = serializers.IntegerField(min_value=1, max_value=BATCH_MAX_EVENT_COUNT)


class BatchRequestSerializer(serializers.Serializer):
    events = BatchEventSerializer(many=True)

    def validate_events(self, value):
        if len(value) > BATCH_MAX_EVENTS:
            raise serializers.ValidationError(f"A batch can carry at most {BATCH_MAX_EVENTS} events.")
        return value
