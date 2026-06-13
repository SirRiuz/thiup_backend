# Django
from rest_framework import serializers


class MaskProfileSerializer(serializers.Serializer):
    """
    Minimal profile of a mask for the hover card (and base of the future
    Profile screen): id, join date and posts/replies counts.

    posts_count / replies_count are NOT model fields: they come annotated on
    the queryset (Count with filter, a single query — no N+1).
    """

    mask_id = serializers.CharField(source="hash")
    joined = serializers.DateTimeField(source="create_at")
    posts_count = serializers.IntegerField()
    replies_count = serializers.IntegerField()
