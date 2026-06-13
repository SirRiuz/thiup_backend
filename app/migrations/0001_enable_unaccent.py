# Enables PostgreSQL's unaccent extension (CREATE EXTENSION IF NOT
# EXISTS unaccent). Needed for the __unaccent lookup used in the accent-
# insensitive search. Does not alter tables.
from django.db import migrations
from django.contrib.postgres.operations import UnaccentExtension


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        UnaccentExtension(),
    ]
