# Django
from django.contrib import admin
from django.utils.html import format_html

# Models
from apps.reactions.models.reaction_relation import ReactionRelation
from apps.reactions.models.reaction import Reaction


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "is_active",
        "name",
        "preview",
        "create_at",
        "update_at")
    
    def preview(self, obj):
        return format_html(f'<img width="50" src="{obj.icon.url}"/>')


@admin.register(ReactionRelation)
class ReactionRelationsAdmin(admin.ModelAdmin):
    search_fields = ("id", "user", "thread__id")
    list_display = (
        "id",
        "is_active",
        #"user",
        "thread",
        "reaction_prev",
        "create_at",
        "update_at")
        
    def reaction_prev(self, obj):
        return format_html(f'<img width="50" src="{obj.reaction.icon.url}"/>')
    
    reaction_prev.short_description = "reaction"
