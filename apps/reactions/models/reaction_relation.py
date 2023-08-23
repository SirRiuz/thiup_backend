# Django
from django.db import models

# Libs
from apps.default.models.base_model import BaseModel
from apps.threads.models.thread import Thread
from apps.reactions.models.reaction import Reaction


class ReactionRelation(BaseModel):
    thread = models.ForeignKey(
        to=Thread,
        on_delete=models.CASCADE,
        help_text="Reaction thread")
    user = models.CharField(
        max_length=250,
        null=False,
        blank=False,
        help_text="User creator")
    reaction = models.ForeignKey(
        to=Reaction,
        on_delete=models.CASCADE)
