# Django
from rest_framework import serializers

# Libs
from app.methods.presence import is_online

# Models
from app.models.mask import Mask


class MaskSerializer(serializers.ModelSerializer):
    # Ephemeral presence: True if this mask made any request in the last 60 s
    # (LocMem lookup, ~1 microsecond per row — no DB, no network). ADDITIVE
    # field: the frontend renders the "online now" green dot from it.
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = Mask
        # Explicit allowlist: only the hash (the public @id is its first 6
        # chars; the frontend also uses the full value to fetch the hover
        # card) and the presence flag. The internal UUID pk, uid and
        # country_code used to ship on every card — the UUID violated the
        # "only public identifiers leave the API" rule and none of them were
        # read by the frontend.
        fields = ("hash", "is_online")

    def get_is_online(self, obj) -> bool:
        return is_online(obj.hash)
