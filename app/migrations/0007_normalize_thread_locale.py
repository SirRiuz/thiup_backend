# Normalizes language/region of existing threads to their canonical form
# (language lowercase, region uppercase). Any value that came in
# unnormalized (manual admin edits prior to the canonical save()) broke
# the For You language filter, which compares exact and indexed.

from django.db import migrations


def normalize_locale(apps, schema_editor):
    Thread = apps.get_model("app", "Thread")
    table = Thread._meta.db_table
    schema_editor.execute(
        f"UPDATE {table} SET language = LOWER(TRIM(language)), "
        f"region = UPPER(TRIM(region))"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0006_thread_language_alter_thread_region'),
    ]

    operations = [
        migrations.RunPython(normalize_locale, migrations.RunPython.noop),
    ]
