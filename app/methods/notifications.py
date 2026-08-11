import itertools

from app.models.notification import Notification


def group_notifications(notifications) -> list:
    """Collapse CONSECUTIVE same-(thread, verb) rows on an already-fetched,
    -create_at ordered page into one display group.

    Pure Python pass over the page (size 25, PageNumberPagination) — no
    extra queries, every attribute used here is already `select_related`-
    hydrated by the caller. Same "post-fetch enrichment" spirit as
    `attach_top_replies` (app/methods/threads.py) but simpler: nothing left
    to fetch.
    """
    groups = []
    for (thread_id, verb), rows in itertools.groupby(notifications, key=lambda n: (n.thread_id, n.verb)):
        rows = list(rows)
        newest = rows[0]

        actors = []
        seen_actor_ids = set()
        for row in rows:
            if row.actor_id not in seen_actor_ids:
                seen_actor_ids.add(row.actor_id)
                actors.append(row.actor)

        groups.append(
            {
                "thread": newest.thread,
                "verb": verb,
                "actors": actors[:3],
                "actor_count": len(actors),
                "reaction": newest.reaction if verb == Notification.REACTION else None,
                # A group reads as unread until EVERY row in it has been
                # marked read — mark-read flips all of a recipient's unread
                # rows in one bulk update, so in practice a group is never
                # "partially read" by the time it's rendered again.
                "is_read": all(row.is_read for row in rows),
                "create_at": newest.create_at,
            }
        )
    return groups
