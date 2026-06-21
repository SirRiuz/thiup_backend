# Django
from django.contrib import admin

# Models
from app.models.thread import Thread
from app.models.media import ThreadFile
from app.models.mask import Mask
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation
from app.models.tag import Tag
from app.models.momentum_log import MomentumLog
from app.models.trending_tag import TrendingTag
from app.models.report import Report

# Libs
import flag
from app.constants.threads import UNKNOWN_MEDIA_FORMAT


class BaseModelAdmin(admin.ModelAdmin):
    """
    Base for all admins: the BaseModel fields (id, uid,
    create_at, update_at) are SHOWN in the form but cannot be edited —
    they're system-generated (auto-assigned uuid/uid, auto_now
    timestamps). is_active is kept editable on purpose: it's the soft-delete
    and the admin is the place to moderate content.
    """

    BASE_READONLY_FIELDS = ("id", "uid", "create_at", "update_at")

    # Stable ordering for changelists and autocomplete results (the
    # models don't define Meta.ordering; without this the admin pagination
    # may repeat/skip rows — UnorderedObjectListWarning).
    ordering = ("-create_at",)

    def get_readonly_fields(self, request, obj=None):
        # Concatenates with each admin's own readonly fields (e.g. the
        # momentum fields in ThreadAdmin) without duplicates.
        own = tuple(super().get_readonly_fields(request, obj))
        return own + tuple(
            f for f in self.BASE_READONLY_FIELDS if f not in own
        )


@admin.register(Thread)
class ThreadAdmin(BaseModelAdmin):

    list_display = (
        "id",
        "is_active",
        "mask",
        "text",
        "attach_files",
        "sub",
        # For You engine: visible for inspection, never editable (written
        # by the recompute_momentum job — see readonly_fields).
        "momentum_score",
        "unique_reactors_count",
        "unique_commenters_count",
        "create_at",
        "update_at",
    )

    # Precomputed by the scheduled recompute_momentum run: shown in the
    # form but not editable — a manual value would be overwritten on the
    # next run and would break the score/counters consistency.
    readonly_fields = (
        "momentum_score",
        "unique_reactors_count",
        "unique_commenters_count",
    )

    search_fields = (
        "text",
        "uid",
        "sub__uid",
        "mask__hash",
    )

    # FKs with a search box (select2) instead of the <select> with ALL the rows.
    autocomplete_fields = ("sub", "mask")

    def attach_files(self, instance):
        return ThreadFile.objects.filter(is_active=True, thread=instance).count()


@admin.register(ThreadFile)
class ThreadFileAdmin(BaseModelAdmin):

    list_display = (
        "is_active",
        "id",
        "thread",
        "extension",
        "create_at",
        "update_at",
    )

    def extension(self, instance):
        file = instance.file
        return file.url.split(".")[1] if file else UNKNOWN_MEDIA_FORMAT

    search_fields = ("uid", "thread__uid")
    autocomplete_fields = ("thread",)


@admin.register(Mask)
class MaskAdmin(BaseModelAdmin):

    list_display = (
        "id",
        "is_active",
        "mask",
        "country_flag",
        "create_at",
        "update_at",
    )

    search_fields = ("hash", "country_code")

    def mask(self, obj) -> str:
        return str(obj)

    def country_flag(self, obj):
        try:
            return flag.flag(obj.country_code)
        except Exception:
            return flag.flag("Unknow")


@admin.register(Reaction)
class ReactionAdmin(BaseModelAdmin):
    list_display = ("id", "is_active", "name", "emoji", "create_at", "update_at")

    # Target of the ReactionRelation.reaction autocomplete.
    search_fields = ("name", "emoji")


@admin.register(ReactionRelation)
class ReactionRelationsAdmin(BaseModelAdmin):
    # NOTE: it previously searched by "user", a nonexistent field (the model uses mask)
    # — any search in the changelist would have raised FieldError.
    search_fields = ("uid", "mask__hash", "thread__uid")
    autocomplete_fields = ("thread", "mask", "reaction")
    list_display = ("id", "is_active", "thread", "reaction_prev", "create_at", "update_at")

    def reaction_prev(self, obj):
        return obj.reaction.emoji

    reaction_prev.short_description = "reaction"


@admin.register(Tag)
class TagsAdmin(BaseModelAdmin):
    list_display = ("is_active", "name", "thread", "create_at", "update_at")
    search_fields = ("name", "thread__uid")
    autocomplete_fields = ("thread",)


@admin.register(MomentumLog)
class MomentumLogAdmin(BaseModelAdmin):
    """
    Log of the momentum recompute: READ-ONLY. The records are created
    only by the management command `recompute_momentum` (run by the external
    scheduler every 10 min) — from the admin they can't be created or edited;
    deleting is allowed, as housekeeping of old logs.
    """

    list_display = (
        "create_at",
        "was_successful",
        "window_days",
        "processed_count",
        "updated_count",
        "duration_ms",
    )
    list_filter = ("was_successful",)
    date_hierarchy = "create_at"

    def has_add_permission(self, request) -> (bool):
        # Only the scheduled recompute_momentum run creates records.
        return False

    def has_change_permission(self, request, obj=None) -> (bool):
        # No editing: the changelist offers "View" instead of "Change".
        return False


@admin.register(TrendingTag)
class TrendingTagAdmin(BaseModelAdmin):
    """
    Tendencias precomputadas del autocomplete: SOLO LECTURA. La tabla la
    REESCRIBE entera el cron `recompute_momentum` (scheduler externo, /10 min);
    crear o editar a mano no tiene sentido — se pisaría en la próxima
    corrida. Útil para inspeccionar qué tags están en tendencia y su score.
    """

    list_display = ("name", "score", "thread_count", "update_at")
    search_fields = ("name", "name_norm")
    ordering = ("-score",)

    def has_add_permission(self, request) -> (bool):
        return False

    def has_change_permission(self, request, obj=None) -> (bool):
        return False


@admin.register(Report)
class ReportAdmin(BaseModelAdmin):
    """
    Thread reports: VIEW and DELETE only — add/change are disabled (reports are
    created/updated by users through the API, never hand-authored in the admin).

    Anonymity: the `reporter` mask is deliberately NOT shown. Moderation acts on
    the thread + report, never on "who" the (pseudonymous) reporter is.

    TODO(moderation): `is_priority` marks "minors" reports — they require a
    SEPARATE priority/legal review workflow (not built here). They are filtered
    and sorted first so moderation sees them at the top.
    """

    list_display = (
        "thread", "category", "is_priority", "short_reason",
        "create_at", "update_at")
    list_filter = ("is_priority", "category")
    search_fields = ("thread__uid",)
    # Priority (minors) first, then newest.
    ordering = ("-is_priority", "-create_at")
    # Never expose the reporter mask (anonymity).
    exclude = ("reporter",)

    def short_reason(self, obj) -> str:
        text = obj.reason or ""
        return (text[:60] + "…") if len(text) > 60 else text
    short_reason.short_description = "reason"

    def has_add_permission(self, request) -> (bool):
        return False

    def has_change_permission(self, request, obj=None) -> (bool):
        return False
