# Django
from rest_framework import serializers


# Models
from app.models.mask import Mask

# Libs
from app.methods.presence import is_online


class MaskSerializer(serializers.ModelSerializer):
    # Ephemeral presence: True if this mask made any request in the last 60 s
    # (LocMem lookup, ~1 microsecond per row — no DB, no network). ADDITIVE
    # field: the frontend renders the "online now" green dot from it.
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = Mask
        exclude = ("create_at", "update_at", "is_active")

    def get_is_online(self, obj) -> bool:
        return is_online(obj.hash)
