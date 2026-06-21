# Django
from rest_framework import serializers

# Models
from app.models.report import Report


class ReportSerializer(serializers.Serializer):
    """Validates the report payload. The reporter is NEVER part of the input —
    it is derived server-side from the request's pseudonymous mask."""

    thread_id = serializers.CharField(max_length=12)
    category = serializers.ChoiceField(
        choices=[choice[0] for choice in Report.CATEGORY_CHOICES])
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=Report.REASON_MAX_LENGTH)
