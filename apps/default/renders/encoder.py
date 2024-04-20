# Python
import json
import base64

# Django
from rest_framework.renderers import BaseRenderer

# Libs
from apps.default.cripto.kdf import encryptor


class EncodeRenderer(BaseRenderer):
    """
    It is responsible for deciphering the body
    of the response
    """
    media_type = "application/raw"
    format = "custom"

    def render(self, data, media_type=None, renderer_context=None) -> (str):
        key, data, iv = encryptor(json.dumps(data, indent=2))
        response = renderer_context["response"]
        headers = response.headers
        headers["X-Response-Payload"] = base64.b64encode(
            f"{key}:{iv}".encode()).decode()[::-1]

        return data[::-1]
