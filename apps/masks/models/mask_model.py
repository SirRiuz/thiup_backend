# Django
from django.db import models

# Libs
from apps.default.models.base_model import BaseModel
from apps.masks.models.miniature_model import Miniature


class Mask(BaseModel):

    hash = models.CharField(unique=True, max_length=250)
    country_code = models.CharField(max_length=50, help_text="Country of mask")
    miniature = models.ForeignKey(to=Miniature, on_delete=models.SET_NULL, null=True)

    def __str__(self) -> (str):
        return f"@{self.hash[0:6]}"
