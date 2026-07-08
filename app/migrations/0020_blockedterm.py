import re
import uuid

import app.models.base_model
from django.db import migrations, models
from django.utils import timezone

from app.utils.text import strip_accents

# Seed blocklist (shadowban filter): child sexual exploitation, violent
# extremism glorification and hate slurs / incitement — the categories this
# app refuses to host. Matching is WHOLE-WORD, case- and accent-insensitive
# (both sides normalized with strip_accents + lower), so keep entries
# specific: a broad or ambiguous word here hides innocent conversations,
# and the swept threads are LATER HARD-DELETED by the purge GC. Extend or
# trim the live list from the admin (BlockedTerm), not by editing history.
SEED_TERMS = (
    # Child sexual abuse / exploitation
    "pedofilia",
    "pedofilo",
    "pedofilos",
    "pedofila",
    "pederasta",
    "pederastia",
    "pornografia infantil",
    "porno infantil",
    "porno de menores",
    "sexo con menores",
    "sexo con niños",
    "child porn",
    "childporn",
    "child pornography",
    "kiddie porn",
    "kidporn",
    "csam",
    "lolicon",
    "shotacon",
    "pedophile",
    "pedophilia",
    "paedophile",
    "sexo infantil",
    "porno de niñas",
    "porno de niños",
    "niñas desnudas",
    "niños desnudos",
    "menores desnudas",
    "menores desnudos",
    "pack de niñas",
    "packs de niñas",
    "pack de niños",
    "pack de menores",
    "packs de menores",
    # Coded terms / abbreviations used by predators to evade filters.
    # NOTE: "cp" is deliberately included even though it collides with
    # rare innocent uses (postal-code shorthand) — on an anonymous social
    # network the abuse signal outweighs the collateral. Deliberately NOT
    # included: "pedo" (common Spanish slang for fart/drunk), "loli" /
    # "lolita" (common Spanish nicknames), "pdf" (the file format), "map"
    # (the common word) — those would shadowban innocent conversations.
    "cp",
    "caldo de pollo",
    "cheese pizza",
    "baggets",
    "bagets",
    "jailbait",
    "pthc",
    "ptsc",
    "hussyfan",
    "raygold",
    "r@ygold",
    "childlover",
    "childlove",
    "boylover",
    "girllover",
    "minor attracted",
    # Violent extremism / terrorism glorification
    "heil hitler",
    "sieg heil",
    "1488",
    "white power",
    "poder blanco",
    "white supremacy",
    "supremacia blanca",
    "orgullo ario",
    "aryan pride",
    "aryan brotherhood",
    "untermensch",
    "hitler tenia razon",
    "hitler was right",
    "viva hitler",
    "ku klux klan",
    "kkk",
    "limpieza etnica",
    "ethnic cleansing",
    "white genocide",
    "genocidio blanco",
    "race war",
    "guerra racial",
    "day of the rope",
    "viva isis",
    "long live isis",
    # Hate slurs / incitement. Whole-word match means plurals are separate
    # entries. Deliberately NOT included: "coon" (Maine Coon cats), "fag"
    # (UK slang for cigarette), "dyke" (also a reclaimed identity term) —
    # ambiguous words shadowban innocent posts permanently.
    "nigger",
    "niggers",
    "faggot",
    "faggots",
    "kike",
    "kikes",
    "spic",
    "spics",
    "wetback",
    "wetbacks",
    "beaner",
    "beaners",
    "chink",
    "chinks",
    "gook",
    "gooks",
    "raghead",
    "towelhead",
    "tranny",
    "trannies",
    "shemale",
    "porch monkey",
    "jigaboo",
    "sudaca",
    "sudacas",
    "negro de mierda",
    "negra de mierda",
    "negros de mierda",
    "negras de mierda",
    "indio de mierda",
    "india de mierda",
    "indios de mierda",
    "moro de mierda",
    "mora de mierda",
    "moros de mierda",
    "gitano de mierda",
    "gitana de mierda",
    "gitanos de mierda",
    "veneco de mierda",
    "venecos de mierda",
    "maricon de mierda",
    "maricones de mierda",
    "matar negros",
    "matar musulmanes",
    "matar gays",
    "matar inmigrantes",
    "matar mujeres",
    "matar venecos",
    "matar moros",
    "matar gitanos",
    "muerte a los negros",
    "muerte a los gays",
    "muerte a los inmigrantes",
)


def seed_and_sweep(apps, schema_editor):
    """Seed the blocklist and shadowban the existing content carrying it.

    Historical models don't run BlockedTerm.save() (nor fire the live
    post_save sweep signal), so term_norm is derived here with the SAME
    function the model uses, and the initial sweep is replicated inline:
    whole-word match (Postgres \\y boundaries) with a plain __contains
    prefilter so the GIN trigram indexes narrow the candidates.
    NOTE: the swept rows become is_active=False and the purge_inactive GC
    will hard-delete them — the sweep is intentionally NOT reversible.
    """
    BlockedTerm = apps.get_model("app", "BlockedTerm")
    Thread = apps.get_model("app", "Thread")
    Tag = apps.get_model("app", "Tag")

    now = timezone.now()
    for term in SEED_TERMS:
        norm = " ".join(strip_accents(term).lower().split())
        BlockedTerm.objects.get_or_create(term=term, defaults={"term_norm": norm})
        pattern = rf"\y{re.escape(norm)}\y"
        Thread.objects.filter(
            is_active=True,
            text_norm__contains=norm,
            text_norm__regex=pattern,
        ).update(is_active=False, update_at=now)
        Tag.objects.filter(
            is_active=True,
            name_norm__contains=norm,
            name_norm__regex=pattern,
        ).update(is_active=False, update_at=now)


def unseed(apps, schema_editor):
    # Only the seeded terms are removed; the sweep is one-way (rows may
    # already be purged).
    BlockedTerm = apps.get_model("app", "BlockedTerm")
    BlockedTerm.objects.filter(term__in=SEED_TERMS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0019_systemmetrics"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlockedTerm",
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
                ("term", models.CharField(help_text="Blocked word or phrase, as written by the moderator.", max_length=100, unique=True)),
                (
                    "term_norm",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        editable=False,
                        help_text="Derived: lowercase, accent-stripped term (match key).",
                        max_length=100,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.RunPython(seed_and_sweep, unseed),
    ]
