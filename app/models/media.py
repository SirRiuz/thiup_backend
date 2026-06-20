# Python
import os
import uuid

# Django
from django.db import models
from django.core.validators import FileExtensionValidator

# Libs
from app.models.base_model import BaseModel
from app.constants.threads import ALLOWED_MEDIA_FORMATS


def thread_file_upload_to(instance, filename: str) -> str:
    """Store uploads under a random name (keeps the extension) so the original
    filename never leaks."""
    ext = os.path.splitext(filename)[1].lower()
    return f"uploads/{uuid.uuid4().hex}{ext}"


class ThreadFile(BaseModel):

    target_color = models.CharField(max_length=250, default="161c1e")
    width = models.IntegerField(default=0, help_text="Width of the file")
    height = models.IntegerField(default=0, help_text="Height of the file")
    is_video = models.BooleanField(default=False)
    thread = models.ForeignKey("app.Thread", on_delete=models.CASCADE)

    file = models.FileField(
        upload_to=thread_file_upload_to,
        validators=[
            FileExtensionValidator(
                ALLOWED_MEDIA_FORMATS,
            ),
        ],
    )

    def __str__(self) -> str:
        return self.uid
