# Django
from django import forms

# Libs
from honeypot.models.login_attempt import LoginAttempt


class LoginForm(forms.ModelForm):
    username = forms.EmailField(required=True)
    password = forms.CharField(required=True)

    class Meta:
        model = LoginAttempt
        fields = ["username", "password"]
