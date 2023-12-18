# Django
from django.db import models
from django.utils import timezone

# Libs
from apps.default.models.base_model import BaseModel

# Models
from apps.masks.models.mask_model import Mask


class Thread(BaseModel):
    
    content = models.JSONField("Content of the thread")
    text = models.TextField(help_text="Text of the thread.")
    visibility = models.BooleanField(
        default=True,
        help_text="This thread can be indexed by the feed."
    )
    
    expire_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Add an expiration date to the thread"
    )
    
    sub = models.ForeignKey(
        to="self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Parent thread"
    )

    mask = models.ForeignKey(to=Mask, on_delete=models.CASCADE, null=True)

    def is_new(self) -> (bool):
        hours = (timezone.now() - self.create_at).total_seconds() // 3600
        return  hours <= 48
    
    def __str__(self):
        return self.id
