# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


class Reaction(BaseModel):
    name = models.CharField(
        max_length=250,
        blank=False,
        null=False,
        unique=True,
        help_text="Name of the reaction")
    
    emoji = models.CharField(
        max_length=10,
        blank=False,
        null=False,
        help_text="Emoji character representing the reaction (e.g., '🔥', '👁', '✊')")

    def __str__(self) -> (str):
        return self.name
