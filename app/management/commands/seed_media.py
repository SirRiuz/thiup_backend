import re
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import models

from app.models.miniature import Miniature

# Matches Django's default `get_alternative_name` output: <stem>_<7 alnum chars>.
# The model fields use upload_to= the same directory we scan, so any prior seed
# run leaves collision-renamed copies behind. Filtering them keeps seed runs
# idempotent.
_DJANGO_COLLISION_SUFFIX = re.compile(r"_[A-Za-z0-9]{7}$")


class Command(BaseCommand):
    help = (
        "Seed Miniature rows from binaries in media/masks/. Idempotent — "
        "skips entries whose `name` already exists. (Las Reactions ya no se "
        "siembran desde media/: el modelo usa emoji y se carga vía fixture "
        "con `make load_fixtures`.)"
    )

    SEEDS = (
        ("masks", "MASKS_MEDIA_DIR", Miniature),
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            choices=[label for label, _, _ in self.SEEDS],
            help="Seed only the given group (default: all).",
        )

    def handle(self, *args, **options):
        only = options.get("only")
        for label, settings_attr, model in self.SEEDS:
            if only and only != label:
                continue
            self._seed_directory(label, getattr(settings, settings_attr), model)

    def _seed_directory(
        self,
        label: str,
        source_dir: str,
        model: type[models.Model],
    ) -> None:
        source = Path(source_dir)
        if not source.is_dir():
            self.stdout.write(self.style.WARNING(f"[{label}] {source} missing — skipped"))
            return

        # Snapshot the listing before any DB write — Django's upload_to points
        # at this same directory, so writes during the loop would otherwise be
        # re-discovered as new sources on the next iteration.
        all_stems = {p.stem for p in source.iterdir() if p.is_file()}

        def is_django_collision_copy(stem: str) -> bool:
            """True if this looks like a previous seed run's collision rename."""
            match = _DJANGO_COLLISION_SUFFIX.search(stem)
            return bool(match) and stem[: match.start()] in all_stems

        files = sorted(p for p in source.iterdir() if p.is_file() and not is_django_collision_copy(p.stem))

        created = skipped = 0
        for path in files:
            name = path.stem
            if model.objects.filter(name=name).exists():
                skipped += 1
                continue
            with path.open("rb") as handle:
                model.objects.create(
                    name=name,
                    icon=ContentFile(handle.read(), path.name),
                )
            created += 1
            self.stdout.write(f"[{label}] created {name}")

        self.stdout.write(self.style.SUCCESS(f"[{label}] done — {created} created, {skipped} skipped"))
