#Django
from django.db import models
from django.core.validators import FileExtensionValidator

#Libs
from apps.default.models.base_model import BaseModel
from apps.threads.constants import ALLOWED_MEDIA_FORMATS


class ThreadFile(BaseModel):

    target_color = models.CharField(max_length=250, default="161c1e")
    width = models.IntegerField(default=0, help_text="Width of the file")
    height = models.IntegerField(default=0, help_text="Height of the file")
    is_video = models.BooleanField(default=False)
    thread = models.ForeignKey("threads.Thread", on_delete=models.CASCADE)
    file = models.FileField(
        upload_to="uploads/",
        validators=[
            FileExtensionValidator(ALLOWED_MEDIA_FORMATS)
        ])

    def __str__(self) -> (str):
        return self.id
