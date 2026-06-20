from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0012_search_norm_trgm_indexes"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="mask",
            name="miniature",
        ),
        migrations.DeleteModel(
            name="Miniature",
        ),
    ]
