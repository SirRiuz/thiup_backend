import django.core.validators
from django.db import migrations, models

import app.models.media


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0013_remove_miniature"),
    ]

    operations = [
        migrations.AlterField(
            model_name="threadfile",
            name="file",
            field=models.FileField(
                upload_to=app.models.media.thread_file_upload_to,
                validators=[
                    django.core.validators.FileExtensionValidator(
                        ("mp4", "png", "jpg", "jpeg")
                    )
                ],
            ),
        ),
    ]
