# Python
import base64
import uuid
import mimetypes
from os import unlink
from io import BytesIO
from tempfile import NamedTemporaryFile

# Django
from django.core.files.base import ContentFile

# Models
from apps.threads.models.media import ThreadFile

def save_files(files, thread):
    """
    This function is responsible for saving
    the multimedia files of a thread.
    """
    for file in files:
        f_bytes = base64.b64decode(file["data"])
        file_name = uuid.uuid4().__hash__().__str__()
        extension = mimetypes.guess_extension(file["type"])
        file_name = f"{file_name}{extension}"
        file_bytes = ContentFile(f_bytes, name=file_name)

        width = file["resolution"]["width"]
        height = file["resolution"]["height"]

        ThreadFile.objects.create(
            thread=thread,
            file=file_bytes,
            is_video=file["is_video"],
            target_color=file["target_color"],
            width=width,
            height=height
        )
