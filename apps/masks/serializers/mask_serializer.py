# Django
from rest_framework import serializers


# Models
from apps.masks.models.mask_model import Mask


class MaskSerializer(serializers.ModelSerializer):

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["miniature"] = instance.miniature.icon.url if \
            instance.miniature else None

        return representation

    class Meta:
        model = Mask
        exclude = ("create_at", "update_at", "is_active")
