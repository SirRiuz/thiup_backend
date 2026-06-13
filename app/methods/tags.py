# Python
import re

# Models
from app.models.tag import Tag

# Libs
from app.utils.text import strip_accents


def get_tags_list(text) -> list:
    """Get the list of the tags of the thread text.

    \\w in Python 3 is already Unicode-aware (includes accented and non-ASCII letters). The
    hyphen is added to match the frontend regex (src/utils/hashtags.js):
    #encrypted-messaging must be stored in full, not cut at the '-'.
    """
    return re.findall(r"#([\w-]+)", text)


def create_tags(thread, tags_list):
    """Create a relation with thread and hashtag"""
    for tag in tags_list:
        name = tag.lower()
        Tag.objects.create(
            name=name,
            # name_norm feeds the autocomplete (indexed prefix,
            # accent-insensitive): lowercase + no accents.
            name_norm=strip_accents(name),
            thread=thread)
