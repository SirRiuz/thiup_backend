# Django
from rest_framework import serializers


class TagSearchSerializer(serializers.Serializer):
    """
    Aggregated search tag: { name, count }.

    `count` = number of distinct threads (posts) that use the tag. It's
    annotated at the query level (Count('thread', distinct=True)) over the Tag
    model (which has one row per (thread, hashtag)), so it is NOT a model field
    and does NOT require a migration. The items arriving here are dicts from
    `.values()`.

    `activity` = the tag's real time series: posts per day for the last
    ACTIVITY_DAYS days (chronological order, days without posts filled with 0).
    The view (_attach_activity) attaches it with a single aggregated query.
    """

    name = serializers.CharField()
    count = serializers.IntegerField()
    activity = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list)


class UserSearchSerializer(serializers.Serializer):
    """
    Search mask/user with what the frontend's user card needs.

    MaskSerializer (which excludes create_at and doesn't bring posts_count) is
    not reused, so as not to couple the search to the serializer shared by posts.
    """

    hash = serializers.CharField()
    country_code = serializers.CharField()
    posts_count = serializers.IntegerField()
    joined_at = serializers.DateTimeField(source="create_at")
    miniature = serializers.SerializerMethodField()

    def get_miniature(self, obj):
        return obj.miniature.icon.url if obj.miniature else None
