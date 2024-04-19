# Django
from django.contrib import admin

# Models
from apps.threads.models.thread import Thread
from apps.threads.models.media import ThreadFile

# Libs
from apps.threads.constants import UNKNOWN_MEDIA_FORMAT


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "is_active",
        "mask",
        "text",
        "attach_files",
        "sub",
        "create_at",
        "update_at")

    search_fields = ("text", "id", "sub__id", "mask__hash")

    def attach_files(self, instance):
        return ThreadFile.objects.filter(
            is_active=True,
            thread=instance).count()


@admin.register(ThreadFile)
class ThreadFileAdmin(admin.ModelAdmin):
    list_display = (
        "is_active",
        "id",
        "thread",
        "extension",
        "create_at",
        "update_at")

    def extension(self, instance):
        file = instance.file
        return (file.url.split(".")[1] if
                file else UNKNOWN_MEDIA_FORMAT)

    search_fields = ("id", "thread__id")
