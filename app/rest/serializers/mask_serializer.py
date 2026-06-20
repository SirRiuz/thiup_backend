# Django
from rest_framework import serializers


# Models
from app.models.mask import Mask


class MaskSerializer(serializers.ModelSerializer):

    class Meta:
        model = Mask
        exclude = ("create_at", "update_at", "is_active")
