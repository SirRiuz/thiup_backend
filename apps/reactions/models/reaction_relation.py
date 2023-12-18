# Django
from django.db import models

# Libs
from apps.default.models.base_model import BaseModel
from apps.threads.models.thread import Thread
from apps.reactions.models.reaction import Reaction
from apps.masks.models.mask_model import Mask


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
