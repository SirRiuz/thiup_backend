# Python
import re
import math
import random

# Geohash without PostGIS: indexed text cells. Precision 5 ≈ cells of
# ~4.9×4.9 km — COARSE enough to not reveal locations (privacy:
# the client also applies fuzzing to the coords before encoding). The
# reader's cell + its 8 neighbors cover ~10-15 km (the Close You radius).

GEOHASH_PRECISION = 5

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_GEOHASH_RE = re.compile(r"^[0-9b-hjkmnp-z]+$")


def normalize_geohash(raw) -> (str):
    """
    Sanitizes a client-declared geohash: lowercase, geohash base32
    charset, truncated to GEOHASH_PRECISION (finer = more privacy
    lost: NEVER store more precision). Invalid/short → None.
    """
    clean = str(raw or "").strip().lower()[:GEOHASH_PRECISION]
    if len(clean) < GEOHASH_PRECISION or not _GEOHASH_RE.match(clean):
        return None
    return clean


# Mirror of the client's fuzzing (src/utils/geohash.js): jitter ±~1 km
# BEFORE encoding — not even the cell is exactly "where the point is".
FUZZ_DEGREES = 0.01


def fuzzed_geohash(lat, lon, precision=GEOHASH_PRECISION) -> (str):
    """
    Geohash with the SAME fuzzing the client applies when creating threads —
    the dummy data stays statistically identical to the real flow and the
    Close You filter captures it exactly the same way.
    """
    return encode_geohash(
        lat + random.uniform(-FUZZ_DEGREES, FUZZ_DEGREES),
        lon + random.uniform(-FUZZ_DEGREES, FUZZ_DEGREES),
        precision,
    )


def encode_geohash(lat, lon, precision=GEOHASH_PRECISION) -> (str):
    """
    Standard geohash encode (interleave of lon/lat bits → base32).
    In production the POST's geohash is computed by the CLIENT (the backend
    never sees coords) — this exists for dummy data and tests.
    """
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    result = []
    bit = 0
    ch = 0
    even = True  # starts with longitude

    while len(result) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_lo = mid
            else:
                ch = ch << 1
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_lo = mid
            else:
                ch = ch << 1
                lat_hi = mid
        even = not even
        bit += 1
        if bit == 5:
            result.append(_BASE32[ch])
            bit = 0
            ch = 0

    return "".join(result)


# ── Expandable radius (Close You) ────────────────────────────────────────
# The number of cells in a geohash filter grows with radius²: to keep
# the query BOUNDED across the whole 1-100 km range, the precision adapts:
#   radius ≤ 25 km → precision 5 (cell ~4.9×4.9 km) → grid ≤ 13×13 = 169
#   radius > 25 km → precision 4 (cell ~19.5×39 km) → grid ≤ 13×7  =  91
# (at P4 the filter uses the derived field Thread.geohash4, indexed).
# NOTE: geohash cells are NOT square (P4 is 19.5 km tall × 39 km
# wide) — the rings are computed PER AXIS with the cell's real size,
# or a 100 km radius would only cover ~58 km to the north.
PRECISION_SWITCH_KM = 25.0

_KM_PER_LAT_DEGREE = 110.574
_KM_PER_LON_DEGREE_EQ = 111.32


def decode_geohash_cell(geohash) -> (tuple):
    """Center and size of the cell: (lat, lon, lat_size, lon_size)."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True

    for char in geohash:
        bits = _BASE32.index(char)
        for i in range(4, -1, -1):
            bit = (bits >> i) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                if bit:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if bit:
                    lat_lo = mid
                else:
                    lat_hi = mid
            even = not even

    return (
        (lat_lo + lat_hi) / 2,
        (lon_lo + lon_hi) / 2,
        lat_hi - lat_lo,
        lon_hi - lon_lo,
    )


def cells_for_radius(center_geohash, radius_km) -> (tuple):
    """
    Grid of cells covering `radius_km` around the reader's cell,
    with ADAPTIVE precision (see constants above).

    Returns (ring_by_cell, precision):
      ring_by_cell: {cell: normalized distance 0.0-1.0 to the center
        (Chebyshev per axis)} — feeds the decreasing proximity
        boost: 0.0 = the reader's cell, 1.0 = edge of the radius.
      precision: 5 (filter by geohash) or 4 (filter by geohash4).
    """
    if radius_km <= PRECISION_SWITCH_KM:
        precision = GEOHASH_PRECISION
        base = center_geohash
    else:
        precision = GEOHASH_PRECISION - 1
        base = center_geohash[: GEOHASH_PRECISION - 1]

    lat, lon, lat_size, lon_size = decode_geohash_cell(base)

    # REAL cell size in km (the width depends on latitude) →
    # rings per axis: coverage is correct in both directions.
    lat_km = lat_size * _KM_PER_LAT_DEGREE
    lon_km = max(
        0.001,
        lon_size * _KM_PER_LON_DEGREE_EQ * math.cos(math.radians(lat)),
    )
    rings_lat = max(1, math.ceil(radius_km / lat_km))
    rings_lon = max(1, math.ceil(radius_km / lon_km))

    ring_by_cell = {}
    for di in range(-rings_lat, rings_lat + 1):
        for dj in range(-rings_lon, rings_lon + 1):
            cell_lat = max(-90.0, min(90.0, lat + di * lat_size))
            cell_lon = ((lon + dj * lon_size + 540.0) % 360.0) - 180.0
            cell = encode_geohash(cell_lat, cell_lon, precision)
            ring = max(abs(di) / rings_lat, abs(dj) / rings_lon)
            # World edges: the clamp/wrap may repeat a cell — the
            # closest distance is kept.
            if cell not in ring_by_cell or ring < ring_by_cell[cell]:
                ring_by_cell[cell] = ring

    return ring_by_cell, precision
