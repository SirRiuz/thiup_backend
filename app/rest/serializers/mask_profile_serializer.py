# Django
from rest_framework import serializers

# Libs
from app.methods.presence import is_online


class MaskProfileSerializer(serializers.Serializer):
    """
    Minimal profile of a mask for the hover card (and base of the future
    Profile screen): id, join date, posts/replies counts and the ephemeral
    "online now" flag.

    posts_count / replies_count are NOT model fields: they come annotated on
    the queryset (Count with filter, a single query — no N+1).
    """

    mask_id = serializers.CharField(source="hash")
    joined = serializers.DateTimeField(source="create_at")
    posts_count = serializers.IntegerField()
    replies_count = serializers.IntegerField()
    # Ephemeral presence (LocMem, 60 s TTL) — additive, boolean only.
    is_online = serializers.SerializerMethodField()

    def get_is_online(self, obj) -> bool:
        return is_online(obj.hash)
