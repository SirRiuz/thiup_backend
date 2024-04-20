# Django
from django.contrib import admin

# Models
from apps.tags.models.tag import Tag


@admin.register(Tag)
class TagsAdmin(admin.ModelAdmin):
    list_display = (
        "is_active",
        "name",
        "thread",
        "create_at",
        "update_at"
    )
