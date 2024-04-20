# Django
from django.conf import settings
from django.core.files.base import ContentFile

# Models
from apps.reactions.models.reaction import Reaction

# Libs
from core.setup.utils.files import get_file_list


def setup_reactions():
    """
    It is responsible for logging the added reactions
    in the reaction file in case they are not created.
    """
    for file in get_file_list(settings.REACTIONS_MEDIA_DIR):
        with open(file["path"], "rb") as f:
            file_name = f'{file["hash"]}.{file["extension"]}'
            reaction_name = file["name"]

            reaction_bytes = ContentFile(f.read(), file_name)
            check_reaction = Reaction.objects.filter(name=reaction_name)

            if not check_reaction:
                print(f"Creating new reaction : {reaction_name}")
                Reaction.objects.create(
                    name=reaction_name,
                    icon=reaction_bytes)
                
            f.close()
