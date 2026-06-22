from django.db import migrations

# New reaction catalog (6), stable internal ids in `name`; the emoji is just a
# default — the frontend owns presentation. Same PKs/uids as
# app/fixtures/reactions.json so `load_fixtures` (loaddata) stays idempotent.
NEW_REACTIONS = [
    ("a1b2c3d4-0001-4a01-9001-000000000001", "loveRxn", "love", "❤️"),
    ("a1b2c3d4-0002-4a02-9002-000000000002", "laughRxn", "laugh", "😂"),
    ("a1b2c3d4-0003-4a03-9003-000000000003", "wowRxn", "wow", "😮"),
    ("a1b2c3d4-0004-4a04-9004-000000000004", "sadRxn", "sad", "😢"),
    ("a1b2c3d4-0005-4a05-9005-000000000005", "angryRxn", "angry", "😡"),
    ("a1b2c3d4-0006-4a06-9006-000000000006", "applauseRx", "applause", "👏"),
]


def replace_reactions(apps, schema_editor):
    """Swap the 5-reaction set for the new 6. The old reactions (fire/seen/
    solidarity/skeptical/heartbreak) have no honest mapping to the new ones, so
    every per-user reaction and the old catalog are dropped, then the new
    catalog is seeded. Leaves the DB with ONLY the 6 valid reactions and no
    relations pointing at removed rows."""
    Reaction = apps.get_model("app", "Reaction")
    ReactionRelation = apps.get_model("app", "ReactionRelation")

    ReactionRelation.objects.all().delete()
    Reaction.objects.all().delete()

    for pk, uid, name, emoji in NEW_REACTIONS:
        Reaction.objects.create(
            id=pk, uid=uid, name=name, emoji=emoji, is_active=True
        )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0014_threadfile_random_filename"),
    ]

    # Irreversible by design: the dropped old reactions/relations can't be
    # recovered, so reverse is a no-op.
    operations = [
        migrations.RunPython(replace_reactions, migrations.RunPython.noop),
    ]
