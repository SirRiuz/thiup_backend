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
    """Create a relation with thread and hashtag.

    ONE bulk INSERT for the whole list (before: one INSERT per hashtag).
    BaseModel's id/uid come from field defaults, so bulk_create fills them.
    """
    tags = [
        Tag(
            name=tag.lower(),
            # name_norm feeds the autocomplete (indexed prefix,
            # accent-insensitive): lowercase + no accents.
            name_norm=strip_accents(tag.lower()),
            thread=thread,
        )
        for tag in tags_list
    ]
    if tags:
        Tag.objects.bulk_create(tags)
