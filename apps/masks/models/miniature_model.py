# Django
from django.db import models

# Libs
from apps.default.models.base_model import BaseModel


class Miniature(BaseModel):

    name = models.CharField(
        max_length=250,
        blank=False,
        null=False,
        unique=True)

    icon = models.ImageField(
        upload_to="miniature", null=False,
        blank=False, help_text="Mask miniature icon")

    def __str__(self) -> (str):
        return self.id
