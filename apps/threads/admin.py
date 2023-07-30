# Django
from django.contrib import admin

# Models
from apps.threads.models.thread import Thread
from apps.threads.models.media import ThreadFile


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "is_active",
        "is_expired",
        "text",
        "attach_files",
        "create_at",
        "update_at")
    
    search_fields = ("text", "id")
    
    def attach_files(self, instance):
        return ThreadFile.objects.filter(
            is_active=True,
            thread=instance).count()
        
    def is_expired(self, instance):
        pass

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
        return (file.url.split(".")[1] if \
            file else "Unknow")
    
    search_fields = ("id", "thread__id")
