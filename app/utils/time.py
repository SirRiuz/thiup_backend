from datetime import datetime
from typing import Optional

from django.utils import timezone


def format_short_time(dt: Optional[datetime]) -> str:
    """Formats a datetime into a compact relative string with 1-letter unit.

    Examples:
        29 minutes ago  -> '29m'
        2 hours ago     -> '2h'
        5 days ago      -> '5d'
        3 weeks ago     -> '3w'
        2 years ago     -> '2Y'
        < 60 seconds    -> 'now'

    Args:
        dt: datetime object (timezone-aware preferred).

    Returns:
        Compact relative time string. Returns 'now' for None or future dates.
    """
    if dt is None:
        return "now"

    delta = timezone.now() - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "now"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"

    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"

    days = hours // 24
    if days < 7:
        return f"{days}d"

    weeks = days // 7
    if weeks < 52:
        return f"{weeks}w"

    years = days // 365
    return f"{years}Y"
