import hashlib
import math
import random
from typing import List, Optional, Tuple
from datetime import timedelta

import geonamescache
import shortuuid
from faker import Faker

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models.mask import Mask
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation
from app.models.tag import Tag
from app.models.thread import Thread
from app.methods.tags import get_tags_list
from app.utils.text import strip_accents
from app.utils.locale import normalize_language, normalize_region
from app.utils.geo import fuzzed_geohash, GEOHASH_PRECISION

# Spanish content (the app is in Spanish; for countries of another language
# the text stays Spanish by default — the language FIELD does reflect the
# country).
fake = Faker("es_CO")

DEFAULT_REACTIONS = [
    {"name": "love", "emoji": "❤️"},
    {"name": "laugh", "emoji": "😂"},
    {"name": "wow", "emoji": "😮"},
    {"name": "sad", "emoji": "😢"},
    {"name": "angry", "emoji": "😡"},
    {"name": "applause", "emoji": "👏"},
]

HASHTAG_POOL = [
    "#dns", "#privacy", "#security", "#tor", "#opsec", "#encryption",
    "#censorship", "#supplychain", "#research", "#linux", "#python",
    "#ai", "#crypto", "#hacking", "#vpn", "#raspberry", "#opensource",
    "#politica", "#elecciones", "#economia", "#inflacion", "#trabajo",
    "#educacion", "#salud", "#clima", "#energia", "#justicia",
    "#periodismo", "#musica", "#cine", "#libros", "#arte", "#gaming",
    "#futbol", "#cocina", "#cafe", "#viajes", "#memes", "#humor",
    "#filosofia", "#historia", "#ciencia", "#espacio", "#random",
]

# ── Barrancabermeja (where the team tests) ──────────────────────────────
# Points at CONTROLLED distances to exercise Close You and radius
# expansion: each band (15/30/50/100 km) has threads just inside and
# just outside.
BARRANCA = (7.0653, -73.8547)
BARRANCA_DISTANCES_KM = (3, 10, 20, 40, 80, 120)
BARRANCA_BEARINGS = (0, 90, 180, 270)

# Real municipalities of the area (in case geonamescache doesn't ship them all).
NEARBY_TOWNS = [
    ("Bucaramanga", 7.1254, -73.1198),
    ("Girón", 7.0689, -73.1736),
    ("Floridablanca", 7.0622, -73.0864),
    ("Piedecuesta", 6.9866, -73.0497),
    ("San Vicente de Chucurí", 6.8773, -73.4115),
    ("Sabana de Torres", 7.3919, -73.4956),
    ("Puerto Wilches", 7.3475, -73.8950),
]

MIN_MASKS_POOL = 60
BULK_BATCH = 500

# Varied ages (hours) to test decay/ordering and the grace window.
AGE_BUCKETS_HOURS = (0.5, 1.5, 4, 10, 24, 48, 96)


def point_at(lat: float, lon: float, distance_km: float,
             bearing_deg: float) -> Tuple[float, float]:
    """Point at `distance_km` and bearing `bearing_deg` (equirectangular —
    good enough for <200 km)."""
    rad = math.radians(bearing_deg)
    dlat = (distance_km * math.cos(rad)) / 111.32
    dlon = (distance_km * math.sin(rad)) / (
        111.32 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


class Command(BaseCommand):
    """
    Geolocated and OPTIMIZED seeding (batched bulk_create, Raspberry-
    friendly):

      · ALL cities of Colombia (geonamescache, offline)
      · configurable worldwide spread (--global-cities)
      · points at controlled distances around Barrancabermeja
      · threads without geo (--number) for the global For You

    Each thread: precision-5 geohash with the SAME client fuzzing +
    derived geohash4 (bulk_create skips save(): set explicitly) +
    country language + region (countrycode) + REAL interactions
    (commenters/reactors) to pass the threshold —
    recompute_momentum runs at the end, so the beat keeps the values
    coherent (seeding momentum by hand would be overwritten in ≤10 min).
    """

    help = (
        "Siembra hilos dummy geolocalizados (Colombia completa + mundo + "
        "área de Barrancabermeja). Args: --global-cities N (default 200), "
        "--number N hilos sin geo (default 10), --clear (borra TODOS los "
        "threads antes)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--global-cities", type=int, default=200,
            help="Ciudades del mundo a muestrear (además de Colombia).",
        )
        parser.add_argument(
            "--number", type=int, default=10,
            help="Hilos extra SIN geolocalizar (For You global).",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Borra TODOS los threads (y sus tags/reacciones) antes.",
        )

    # ── main flow ──────────────────────────────────────────────────────

    def handle(self, *args, **options) -> None:
        if options["clear"]:
            deleted, _ = Thread.objects.all().delete()
            self.stdout.write(f"--clear: {deleted} filas borradas.")

        reactions = self.__ensure_reactions_exist()
        masks = self.__ensure_masks_pool()

        # 1. Thread specs in memory (without touching the DB).
        specs = []
        specs += self.__city_specs(options["global_cities"])
        specs += self.__barranca_specs()
        specs += self.__global_specs(options["number"])
        random.shuffle(specs)

        # 2. Roots in batches (explicit geohash4: bulk skips save()).
        roots = self.__bulk_create_roots(specs, masks)

        # 3. Varied ages: one UPDATE per bucket (auto_now_add overrides the
        #    value on insert; corrected as a group, not row by row).
        self.__spread_ages(roots)

        # 4. Tags, comments (child threads) and reactions — all bulk.
        tags_n = self.__bulk_create_tags(roots)
        comments_n = self.__bulk_create_comments(roots, masks)
        reactions_n = self.__bulk_create_reactions(roots, reactions, masks)

        # 5. REAL momentum from the seeded interactions.
        call_command("recompute_momentum")

        geo_count = sum(1 for s in specs if s["geo"])
        barranca_points = (
            len(BARRANCA_DISTANCES_KM) * len(BARRANCA_BEARINGS)
            + len(NEARBY_TOWNS) + 1
        )
        self.stdout.write(self.style.SUCCESS(
            f"\n✅ Seed geolocalizado:\n"
            f"   {len(roots)} hilos ({geo_count} con geohash)\n"
            f"   {comments_n} comentarios · {reactions_n} reacciones · "
            f"{tags_n} tags\n"
            f"   Colombia completa + {options['global_cities']} ciudades "
            f"del mundo + {barranca_points} puntos del área de "
            f"Barrancabermeja"
        ))

    # ── specs ──────────────────────────────────────────────────────────

    def __city_specs(self, global_count: int) -> List[dict]:
        gc = geonamescache.GeonamesCache()
        cities = list(gc.get_cities().values())
        countries = gc.get_countries()

        def lang_of(countrycode: str) -> str:
            raw = (countries.get(countrycode, {}) or {}).get("languages", "")
            first = raw.split(",")[0].split("-")[0].strip().lower()
            return normalize_language(first)

        colombia = [c for c in cities if c["countrycode"] == "CO"]
        others = [c for c in cities if c["countrycode"] != "CO"]
        # Bounded worldwide spread: sample among the most populated (better
        # country coverage) without inserting tens of thousands.
        others.sort(key=lambda c: -c.get("population", 0))
        pool = others[: max(global_count * 5, global_count)]
        sampled = random.sample(pool, min(global_count, len(pool)))

        return [
            {
                "geo": (city["latitude"], city["longitude"]),
                "place": city["name"],
                "language": lang_of(city["countrycode"]),
                "region": city["countrycode"],
            }
            for city in colombia + sampled
        ]

    def __barranca_specs(self) -> List[dict]:
        lat0, lon0 = BARRANCA
        specs = [{
            "geo": BARRANCA,
            "place": "Barrancabermeja",
            "language": "es",
            "region": "CO",
        }]
        for km in BARRANCA_DISTANCES_KM:
            for bearing in BARRANCA_BEARINGS:
                specs.append({
                    "geo": point_at(lat0, lon0, km, bearing),
                    "place": f"a ~{km} km de Barrancabermeja",
                    "language": "es",
                    "region": "CO",
                })
        for name, lat, lon in NEARBY_TOWNS:
            specs.append({
                "geo": (lat, lon),
                "place": name,
                "language": "es",
                "region": "CO",
            })
        return specs

    def __global_specs(self, count: int) -> List[dict]:
        return [
            {"geo": None, "place": None, "language": "es", "region": "CO"}
            for _ in range(count)
        ]

    # ── bulk creation ──────────────────────────────────────────────────

    def __thread_text(self, place: Optional[str]) -> str:
        sentence = fake.text(max_nb_chars=random.randint(60, 200)).strip()
        prefix = f"Desde {place}: " if place else ""
        tags = ""
        if random.random() < 0.6:
            chosen = random.sample(HASHTAG_POOL, k=random.randint(1, 3))
            tags = " " + " ".join(chosen)
        return f"{prefix}{sentence}{tags}"

    def __bulk_create_roots(self, specs: List[dict],
                            masks: List[Mask]) -> List[Thread]:
        objs = []
        for spec in specs:
            text = self.__thread_text(spec["place"])
            geohash = None
            if spec["geo"]:
                lat, lon = spec["geo"]
                # SAME util and SAME fuzzing as the real client: the
                # Close You filter captures them in exactly the same way.
                geohash = fuzzed_geohash(lat, lon)
            objs.append(Thread(
                text=text,
                content=self.__build_draftjs_content(text),
                # bulk_create skips save(): derive text_norm here or the
                # search (text_norm__contains) wouldn't find these threads.
                text_norm=strip_accents(text).lower(),
                mask=random.choice(masks),
                language=normalize_language(spec["language"]),
                region=normalize_region(spec["region"]),
                geohash=geohash,
                # bulk_create skips save(): derive geohash4 here or the
                # large radii (>25 km) wouldn't find these threads.
                geohash4=geohash[:GEOHASH_PRECISION - 1] if geohash else None,
            ))

        created = []
        for i in range(0, len(objs), BULK_BATCH):
            created += Thread.objects.bulk_create(objs[i:i + BULK_BATCH])
        return created

    def __spread_ages(self, roots: List[Thread]) -> None:
        now = timezone.now()
        buckets = {}
        for thread in roots:
            hours = random.choice(AGE_BUCKETS_HOURS)
            buckets.setdefault(hours, []).append(thread.pk)
        for hours, pks in buckets.items():
            Thread.objects.filter(pk__in=pks).update(
                create_at=now - timedelta(hours=hours))

    def __bulk_create_tags(self, roots: List[Thread]) -> int:
        objs = []
        for thread in roots:
            for name in get_tags_list(thread.text):
                clean = name.lower()
                objs.append(Tag(
                    name=clean,
                    name_norm=strip_accents(clean),
                    thread=thread,
                ))
        for i in range(0, len(objs), BULK_BATCH):
            Tag.objects.bulk_create(objs[i:i + BULK_BATCH])
        return len(objs)

    def __bulk_create_comments(self, roots: List[Thread],
                               masks: List[Mask]) -> int:
        """
        DISTINCT commenters (≠ author) per thread — the signal that weighs
        in the threshold (≥1 commenter) and the momentum (×3). Most threads
        receive at least one (Close You/For You don't come out empty).
        """
        objs = []
        for thread in roots:
            commenters = random.choices(
                (0, 1, 2, 3, 4), weights=(15, 35, 25, 15, 10))[0]
            candidates = [m for m in masks if m.pk != thread.mask_id]
            chosen = random.sample(
                candidates, k=min(commenters, len(candidates)))
            for mask in chosen:
                text = fake.sentence(nb_words=random.randint(4, 14))
                objs.append(Thread(
                    text=text,
                    content=self.__build_draftjs_content(text),
                    text_norm=strip_accents(text).lower(),
                    mask=mask,
                    sub=thread,
                    language="es",
                    region="CO",
                ))
        for i in range(0, len(objs), BULK_BATCH):
            Thread.objects.bulk_create(objs[i:i + BULK_BATCH])
        return len(objs)

    def __bulk_create_reactions(self, roots: List[Thread],
                                reactions: List[Reaction],
                                masks: List[Mask]) -> int:
        objs = []
        for thread in roots:
            reactors = random.choices(
                (0, 1, 2, 3, 4, 6, 9), weights=(10, 15, 20, 20, 15, 12, 8))[0]
            candidates = [m for m in masks if m.pk != thread.mask_id]
            chosen = random.sample(
                candidates, k=min(reactors, len(candidates)))
            for mask in chosen:
                objs.append(ReactionRelation(
                    thread=thread,
                    mask=mask,
                    reaction=random.choice(reactions),
                ))
        for i in range(0, len(objs), BULK_BATCH):
            ReactionRelation.objects.bulk_create(
                objs[i:i + BULK_BATCH], ignore_conflicts=True)
        return len(objs)

    # ── pools ──────────────────────────────────────────────────────────

    def __ensure_reactions_exist(self) -> List[Reaction]:
        result = []
        for data in DEFAULT_REACTIONS:
            reaction, _ = Reaction.objects.get_or_create(
                name=data["name"], defaults={"emoji": data["emoji"]})
            result.append(reaction)
        return result

    def __ensure_masks_pool(self) -> List[Mask]:
        existing = Mask.objects.count()
        if existing < MIN_MASKS_POOL:
            Mask.objects.bulk_create([
                Mask(
                    hash=hashlib.sha256(
                        f"{random.random()}-{shortuuid.uuid()}".encode()
                    ).hexdigest(),
                    country_code=random.choice(
                        ["CO", "MX", "AR", "ES", "US", "BR", "FR", "JP"]),
                )
                for _ in range(MIN_MASKS_POOL - existing)
            ])
        return list(Mask.objects.all()[:MIN_MASKS_POOL * 2])

    def __build_draftjs_content(self, text: str) -> dict:
        return {
            "blocks": [{
                "key": shortuuid.uuid()[:5],
                "data": {},
                "text": text,
                "type": "unstyled",
                "depth": 0,
                "entityRanges": [],
                "inlineStyleRanges": [],
            }],
            "entityMap": {},
        }
