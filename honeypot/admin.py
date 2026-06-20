# Django
from django.contrib import admin

# Libs
from honeypot.models.black_list import BlackList
from honeypot.models.login_attempt import LoginAttempt


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "username",
        "ip_address",
        "path",
        "created_date",
    )


@admin.register(BlackList)
class BlackListAdmin(admin.ModelAdmin):
    pass
