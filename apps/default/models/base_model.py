# Python
import uuid

# Django
from django.db import models


class BaseModel(models.Model):
    is_active = models.BooleanField(default=True)
    create_at = models.DateTimeField(auto_now_add=True)
    update_at = models.DateTimeField(auto_now=True)
    id = models.CharField(
        primary_key=True,
        unique=True,
        default="Empty",
        editable=False,
        max_length=255)
    
    def save(self, *args, **kwargs):
        if self._state.adding:
            self.id = str(uuid.uuid4())
        super().save(*args, **kwargs)

    def disable(self):
        self.is_active = False
        self.save()

    class Meta:
        abstract = True

    