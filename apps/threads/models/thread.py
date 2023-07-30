# Django
from django.db import models

# Libs
from apps.default.models.base_model import BaseModel


class Thread(BaseModel):
    text = models.TextField(help_text="Text of the thread.")
    visibility = models.BooleanField(
        default=True,
        help_text="This thread can be indexed by the feed.")
    
    expire_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Add an expiration date to the thread")
    
    sub = models.ForeignKey(
        to="self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Parent thread"
    )
    
    def __str__(self):
        return self.id
