# Python
import uuid

# Django
from django.db import models

# Libs
import shortuuid


def generate_uid() -> str:
    """Generate a short, URL-friendly unique identifier."""
    return shortuuid.ShortUUID().random(length=12)


class BaseModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    uid = models.CharField(
        max_length=12,
        default=generate_uid,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Short, URL-friendly unique identifier.",
    )

    is_active = models.BooleanField(default=True)
    create_at = models.DateTimeField(auto_now_add=True)
    update_at = models.DateTimeField(auto_now=True)

    def disable(self):
        self.is_active = False
        self.save()

    class Meta:
        abstract = True
