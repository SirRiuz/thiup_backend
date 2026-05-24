from datetime import timedelta

import humanize

# Order matters: plurals before singulars so "minutes" doesn't half-match "minute".
_ABBREVIATIONS = [
    ("seconds", "s"),
    ("second", "s"),
    ("minutes", "min"),
    ("minute", "min"),
    ("hours", "h"),
    ("hour", "h"),
    ("days", "d"),
    ("day", "d"),
    ("months", "mo"),
    ("month", "mo"),
    ("years", "y"),
    ("year", "y"),
]


def short_delta(delta: timedelta) -> str:
    """`humanize.naturaldelta` with unit words shortened ("37 minutes" -> "37 min")."""
    text = humanize.naturaldelta(delta)
    for full, short in _ABBREVIATIONS:
        text = text.replace(full, short)
    return text
