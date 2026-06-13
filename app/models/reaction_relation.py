# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel
from app.models.thread import Thread
from app.models.reaction import Reaction
from app.models.mask import Mask


class ReactionRelation(BaseModel):
    thread = models.ForeignKey(
        to=Thread,
        on_delete=models.CASCADE,
        help_text="Reaction thread")

    mask = models.ForeignKey(
        to=Mask,
        on_delete=models.CASCADE,
        help_text="Mask")

    reaction = models.ForeignKey(
        to=Reaction,
        on_delete=models.CASCADE)
