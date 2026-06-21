import app.models.base_model
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0015_replace_reactions"),
    ]

    operations = [
        migrations.CreateModel(
            name="Report",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "uid",
                    models.CharField(
                        db_index=True,
                        default=app.models.base_model.generate_uid,
                        editable=False,
                        help_text="Short, URL-friendly unique identifier.",
                        max_length=12,
                        unique=True,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("create_at", models.DateTimeField(auto_now_add=True)),
                ("update_at", models.DateTimeField(auto_now=True)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("spam_or_deception", "Spam or deception"),
                            ("harassment", "Harassment or bullying"),
                            ("hate_speech", "Hate speech"),
                            ("dangerous_or_self_harm", "Dangerous content or self-harm"),
                            ("minors", "Content involving minors"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional free-text detail (used for the 'other' category).",
                        max_length=300,
                    ),
                ),
                ("is_priority", models.BooleanField(db_index=True, default=False)),
                (
                    "reporter",
                    models.ForeignKey(
                        help_text="Pseudonymous mask of the reporter (anonymity key).",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="app.mask",
                    ),
                ),
                (
                    "thread",
                    models.ForeignKey(
                        help_text="Reported thread.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="app.thread",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(fields=["is_priority", "-create_at"], name="report_priority_idx"),
        ),
        migrations.AddConstraint(
            model_name="report",
            constraint=models.UniqueConstraint(
                fields=("thread", "reporter"), name="unique_report_per_thread_reporter"
            ),
        ),
    ]
