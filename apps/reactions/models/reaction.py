# Django
from django.db import models

# Libs
from apps.default.models.base_model import BaseModel


class Reaction(BaseModel):
    name = models.CharField(
        max_length=250,
        blank=False,
        null=False,
        unique=True,
        help_text="Name of the reaction")
    
    icon = models.ImageField(
        upload_to="reactions",
        null=False,
        blank=False,
        help_text="Reaction icon")

    def __str__(self) -> (str):
        return self.name
