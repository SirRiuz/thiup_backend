import re

from django.core import management
from django.core.management.base import BaseCommand
from django.core.management.commands import loaddata

FIXTURES = ["reactions"]

# Matches uniqueness violations across Postgres / SQLite / MySQL so we can
# treat them as "already loaded" rather than a hard failure.
_ALREADY_EXISTS_RE = re.compile(
    r"duplicate key|already exists|UNIQUE constraint failed",
    re.IGNORECASE,
)


class Command(BaseCommand):
    """Load predefined fixtures idempotently — silently skips rows that already exist."""

    help = "Loads all fixture files required for initial data setup."

    def handle(self, *args, **options) -> None:
        loaded, skipped, failed = [], [], []

        for name in FIXTURES:
            try:
                management.call_command(loaddata.Command(), name, verbosity=0)
                loaded.append(name)
            except Exception as e:
                if _ALREADY_EXISTS_RE.search(str(e)):
                    skipped.append(name)
                else:
                    failed.append((name, e))

        if failed:
            for name, err in failed:
                self.stdout.write(self.style.ERROR(f"❌ Error loading {name}: {err}"))
            return

        parts = []
        if loaded:
            parts.append(f"{len(loaded)} loaded ({', '.join(loaded)})")
        if skipped:
            parts.append(f"{len(skipped)} already present ({', '.join(skipped)})")
        message = "; ".join(parts) if parts else "nothing to load"
        self.stdout.write(self.style.SUCCESS(f"✅ Fixtures ready — {message}."))
