# Django
from django.conf import settings
from django.core.files.base import ContentFile

# Models
from apps.masks.models.miniature_model import Miniature

# Libs
from core.setup.utils.files import get_file_list


def setup_mask():
    """
    It is responsible for logging the added masks
    in the mask file in case they are not created.
    """
    for file in get_file_list(settings.MASKS_MEDIA_DIR):
        with open(file["path"], "rb") as f:
            file_name = f'{file["hash"]}.{file["extension"]}'
            reaction_name = file["name"]

            reaction_bytes = ContentFile(f.read(), file_name)
            check_reaction = Miniature.objects.filter(name=reaction_name)

            if not check_reaction:
                print(f"Creating new mask : {reaction_name}")
                Miniature.objects.create(
                    name=reaction_name,
                    icon=reaction_bytes)
                
            f.close()
