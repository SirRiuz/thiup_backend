# Python
import json
import base64

# Django
from rest_framework.renderers import BaseRenderer
from rest_framework.utils.encoders import JSONEncoder

# Libs
from app.cripto.kdf import encryptor


class EncodeRenderer(BaseRenderer):
    """
    It is responsible for deciphering the body
    of the response
    """
    media_type = "application/raw"
    format = "custom"

    def render(self, data, media_type=None, renderer_context=None) -> (str):
        # cls=JSONEncoder (DRF's): serializes UUID, datetime, Decimal, etc.
        # just like the normal JSONRenderer — the standard json.dumps blows up
        # with raw UUIDs (500 on /reactions/ only with encryption enabled) and the
        # encrypted response must have the SAME format as the unencrypted one.
        # Compact (no indent, minimal separators): the indent was cosmetic
        # (the data travels encrypted) and cost ~4x the dumps and +30% payload.
        key, data, iv = encryptor(
            json.dumps(data, separators=(",", ":"), cls=JSONEncoder))
        response = renderer_context["response"]
        headers = response.headers
        headers["X-Response-Payload"] = base64.b64encode(
            f"{key}:{iv}".encode()).decode()[::-1]

        return data[::-1]
