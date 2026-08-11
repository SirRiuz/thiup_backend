# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


class Mask(BaseModel):
    hash = models.CharField(unique=True, max_length=250)
    country_code = models.CharField(max_length=50, help_text="Country of mask")

    # Denormalized counter, atomically F()-updated by the notification
    # signals (app/signals/notification_signals.py) and reset on
    # POST /notifications/mark-read/ — avoids a COUNT(*) on every badge read.
    unread_notifications_count = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"@{self.hash[0:6]}"
