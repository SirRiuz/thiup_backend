# Django
from django.db import models

# Libs
from apps.threads.models.thread import Thread
from apps.default.models.base_model import BaseModel


class Tag(BaseModel):

    thread = models.ForeignKey(to=Thread, on_delete=models.CASCADE)
    name = models.CharField(max_length=250)

    def __str__(self) -> (str):
        return f"#{self.name}"
