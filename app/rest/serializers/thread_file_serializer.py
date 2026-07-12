# Django
from rest_framework import serializers

# Constants
from app.constants.threads import UPLOAD_CONTENT_TYPE_EXT

# Whitelist of CONFIGURATION metadata keys the client may send (compression +
# client-side NSFW attributes). Anything outside this set is dropped before
# persisting — we never store arbitrary client payloads, and never EXIF.
ALLOWED_METADATA_KEYS = (
    "archivo",
    "formatoAntes",
    "formatoDespues",
    "dimensiones",
    "pesoAntes",
    "pesoDespues",
    "ahorroPorcentaje",
    "colorPredominante",
    "metadatosAntes",
    "metadatosDespues",
    "prediccion",
    "esNsfw",
    "nsfwPorcentaje",
    "explicito",
    "sugerente",
)

# Per-value length cap (defensive: metadata values are short labels/strings).
METADATA_VALUE_MAX_LEN = 120


def clean_metadata(raw) -> dict:
    """Whitelist keys and coerce values to short strings. Returns {} for junk."""
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key in ALLOWED_METADATA_KEYS:
        if key in raw and raw[key] is not None:
            cleaned[key] = str(raw[key])[:METADATA_VALUE_MAX_LEN]
    return cleaned


class PresignSerializer(serializers.Serializer):
    """Validates the presign request. No thread here: the file is uploaded
    before the thread exists, bound only to the requester's mask. The thread
    relation is created later, at `confirm`."""

    content_type = serializers.ChoiceField(choices=sorted(UPLOAD_CONTENT_TYPE_EXT.keys()))
    is_video = serializers.BooleanField(default=False)
    filename_original = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
        help_text="Original name, only kept in metadata.archivo (optional).",
    )


class ConfirmSerializer(serializers.Serializer):
    """Validates the confirm request. Identifies the pending file by its uid and
    carries the config attributes the client computed."""

    uid = serializers.CharField(max_length=12, help_text="Pending file uid.")
    thread = serializers.CharField(max_length=12, help_text="Thread public uid.")
    is_video = serializers.BooleanField(default=False)
    width = serializers.IntegerField(min_value=0, default=0)
    height = serializers.IntegerField(min_value=0, default=0)
    target_color = serializers.CharField(required=False, allow_blank=True, default="161c1e", max_length=250)
    is_nsfw = serializers.BooleanField(default=False)
    metadata = serializers.DictField(required=False, default=dict)
