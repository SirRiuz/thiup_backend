# Thread.geohash4: precision-4 prefix of the geohash (~39 km/cell),
# indexed — the Close You filter with large radii (>25 km) uses
# geohash4 IN (...) instead of LEFT(geohash,4) (which would be a full scan).
# The backfill populates the existing geolocated threads; new ones
# derive it in Thread.save().

from django.db import migrations, models


def backfill_geohash4(apps, schema_editor):
    Thread = apps.get_model("app", "Thread")
    table = Thread._meta.db_table
    schema_editor.execute(
        f"UPDATE {table} SET geohash4 = LEFT(geohash, 4) "
        f"WHERE geohash IS NOT NULL"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0008_thread_geohash'),
    ]

    operations = [
        migrations.AddField(
            model_name='thread',
            name='geohash4',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Derived 4-char geohash prefix (Close You wide radius).',
                max_length=4,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_geohash4, migrations.RunPython.noop),
    ]
