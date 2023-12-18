# Python
import base64
import uuid
import mimetypes

# Django
from django.core.files.base import ContentFile

# Models
from apps.threads.models.media import ThreadFile


def save_files(files, thread):
    for file in files:
        f_bytes = base64.b64decode(file["data"])
        extension = mimetypes.guess_extension(file["type"])
        file_name = uuid.uuid4().__hash__().__str__()
        file_name = f"{file_name}{extension}"
        file_bytes = ContentFile(f_bytes, name=file_name)

        if extension:
            ThreadFile.objects.create(
                file=file_bytes, thread=thread)
