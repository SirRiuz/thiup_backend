# Django
from django.contrib import admin

# Models
from apps.threads.models.thread import Thread


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = (
        "is_active",
        "id",
        "text",
        "create_at"
    )
    
    search_fields = ("text", "id")
