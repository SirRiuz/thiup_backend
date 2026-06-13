# Thread.region: author's country_code denormalized for the For You
# regional boost (momentum × 1.5 at query time, without touching the
# base momentum). The backfill copies the current region of the author's
# mask to the existing threads — new ones set it at creation.

from django.db import migrations, models


def backfill_region(apps, schema_editor):
    Thread = apps.get_model("app", "Thread")
    Mask = apps.get_model("app", "Mask")
    # Bulk UPDATE with subquery — a single statement, without iterating rows.
    schema_editor.execute(
        f"""
        UPDATE {Thread._meta.db_table} AS t
        SET region = m.country_code
        FROM {Mask._meta.db_table} AS m
        WHERE t.mask_id = m.id AND m.country_code IS NOT NULL
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0004_thread_thread_momentum_desc_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='thread',
            name='region',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Author's country code at creation (For You regional boost).",
                max_length=50,
            ),
        ),
        migrations.RunPython(backfill_region, migrations.RunPython.noop),
    ]
