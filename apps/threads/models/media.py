# Django
from django.db import models
from django.core.validators import FileExtensionValidator

# Libs
from apps.default.models.base_model import BaseModel


class ThreadFile(BaseModel):
    thread = models.ForeignKey("threads.Thread", on_delete=models.CASCADE)
    file = models.FileField(
        upload_to="uploads/",
        validators=[
            FileExtensionValidator([
                'png',
                'jpg',
                'jpeg',
                'mp4'])])
    
    def __str__(self) -> (str):
        return self.id
