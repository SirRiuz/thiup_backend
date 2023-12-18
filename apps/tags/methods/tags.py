# Python
import re

# Models
from apps.tags.models.tag import Tag


def get_tags_list(text) -> list:
    """Get the list of the tags of the thread text"""
    return re.findall(r"#(\w+)", text)


def create_tags(thread, tags_list):
    """Create a relation with thread and hashtag"""
    for tag in tags_list:
        Tag.objects.create(
            name=tag.lower(), thread=thread)
