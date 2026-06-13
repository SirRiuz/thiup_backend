# Django
from rest_framework import serializers

# Models
from app.models.tag import Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("name", "count")
