# Django
from django.contrib import admin
from django.utils.html import format_html

# Models
from apps.masks.models.mask_model import Mask
from apps.masks.models.miniature_model import Miniature

# Libs
import flag


@admin.register(Miniature)
class MaskAdmin(admin.ModelAdmin):

    list_display = ("id", "is_active", "preview", "create_at", "update_at")

    def preview(self, obj):
        return format_html(f'<img width="50" src="{obj.icon.url}"/>')


@admin.register(Mask)
class MaskAdmin(admin.ModelAdmin):

    list_display = ("id", "is_active", "mask", "country_flag", "create_at", "update_at", "preview")
    search_fields = ("id", "hash", "country_code")

    def preview(self, obj):
        if obj.miniature:
            return format_html(f'<img width="50" src="{obj.miniature.icon.url}"/>')    

    def mask(self, obj) -> str:
        return str(obj)

    def country_flag(self, obj):
        try:
            return flag.flag(obj.country_code)
        except Exception as e:
            return flag.flag("Unknow")
