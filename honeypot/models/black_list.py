# Django
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

# Models
from app.models.base_model import BaseModel

# Libs
from honeypot.app_settings import HONEYPOT_LOGIN_TRYOUT
from honeypot.models.login_attempt import LoginAttempt


class BlackList(BaseModel):
    created_date = models.DateTimeField(_("created_date"), auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        _("ip address"),
        protocol="both",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ("-created_date",)

    def __str__(self):
        return self.ip_address


@receiver(post_delete, sender=BlackList)
def remove_all_related_attempts(sender, instance, **kwargs):
    LoginAttempt.objects.filter(ip_address=instance.ip_address).delete()


@receiver(post_save, sender=LoginAttempt)
def create_blacklist(sender, instance, created, **kwargs):
    if created and LoginAttempt.objects.filter(ip_address=instance.ip_address).count() >= HONEYPOT_LOGIN_TRYOUT:
        BlackList.objects.get_or_create(ip_address=instance.ip_address)
