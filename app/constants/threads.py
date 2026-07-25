VIDEO_FORMAT = "video"
UNKNOWN_MEDIA_FORMAT = "Unknown"

ALLOWED_VIDEO_FORMAT = ("mp4", "mov")
ALLOWED_IMAGE_FORMAT = (
    "png",
    "jpg",
    "jpeg",
    # The frontend compresses images to WebP before uploading, so the stored
    # reference (and the direct-to-bucket upload) is a .webp object.
    "webp",
)

ALLOWED_MEDIA_FORMATS = ALLOWED_VIDEO_FORMAT + ALLOWED_IMAGE_FORMAT

# Content types accepted for the presigned direct-to-bucket upload, mapped to
# the extension stored in the object key. Kept a strict subset of
# ALLOWED_MEDIA_FORMATS so the presign can only ever sign a known media type.
UPLOAD_CONTENT_TYPE_EXT = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
    "video/mp4": "mp4",
    # QuickTime / iPhone .mov (standard MIME for .mov is video/quicktime).
    "video/quicktime": "mov",
}

# For You entry threshold.
# SEPARATE from momentum: momentum SORTS, the threshold decides who ENTERS.
# A post enters if it meets AT LEAST ONE:
#   unique_commenters ≥ 1  OR  unique_reactions ≥ 3  OR  age < 2h
# (the grace window lets new posts receive their first interactions). All
# fields are precomputed by `recompute_momentum`: the WHERE is over indexed
# counters, recomputing nothing per request.
# Thread/reply text limit — Threads' exact 500 (their posts and replies share
# one limit; a reply IS a Thread here too). Enforced server-side by the
# serializer (the FE composers mirror it with their MAX_CHARS; keep in sync).
# INPUT-only: legacy longer rows still serialize fine.
THREAD_TEXT_MAX_LENGTH = 500

FORYOU_MIN_COMMENTERS = 1
FORYOU_MIN_REACTORS = 3
FORYOU_GRACE_HOURS = 2

# Feed conversation preview (X-style two-story card): a third-party DIRECT
# reply rides under its root's feed card as `top_reply` ONLY when it EARNED
# it — rule B of attach_top_replies (rule A, the author's own continuation,
# never thresholds). TWO gates, both required:
#
#   1. ABSOLUTE FLOOR — at least this many unique reactors, excluding the
#      reply's own author (the momentum engine's anti-self-boost rule).
#   2. RELATIVE BAR — the reply's reactors must be at least this fraction of
#      the ROOT's precomputed unique_reactors_count. This is what keeps the
#      preview scarce AT SCALE: a fixed number alone stops filtering once
#      production threads routinely collect a handful of reactions per reply
#      (a 4-reactor reply is noise on a 50-reactor thread, notable on a
#      4-reactor one). Self-calibrating: the more a thread grows, the more
#      it demands of its replies. A 0-reactor root passes trivially — the
#      reply OUTSHINES the post, X's purest case (the reply drags the post).
#
# Next knob if previews still read too common with real data: additionally
# require the reply to have replies of its own (conversation signal — the
# heaviest ranking signal on X per their published pipeline).
FEED_TOP_REPLY_MIN_REACTORS = 2
FEED_TOP_REPLY_ROOT_RATIO = 0.5

# For You personalization (PHASE 2).
# The client sends its top tags in the POST BODY — the affinity profile,
# which lives ONLY in its localStorage. For the server they are EPHEMERAL:
# they live only as long as this request, like a multi-tag search — they
# are NOT persisted, NOT associated with any mask, there is NO profile
# table.
#
# The algorithm separates two things:
#   STEP 1 (composition): the CANDIDATES are the UNION of the global top by
#     momentum (discovery) + the posts with the user's tags (even if their
#     base momentum is low — if they don't enter, the boost couldn't lift
#     them). Each group bounded to FORYOU_MIX_POOL.
#   STEP 4 (ordering): ALL candidates are sorted together by momentum_final
#     DESC, computed AT SERVE TIME:
#       momentum_final = base × region_boost × affinity_boost
# Tag cap per request: with POST there is no longer a URL limit, but
# filtering/counting against many tags is heavy in the query (Raspberry) —
# the client sends its top ~10 and this truncates defensively.
FORYOU_TAGS_MAX = 15
# Max candidate pool PER GROUP: bounds memory/CPU per request on limited
# hardware (nobody paginates beyond ~500 posts in one session).
FORYOU_MIX_POOL = 500

# Affinity boost (weight of YOUR tags in the ranking).
# affinity_boost = 1 + K × matching_tags (capped at FORYOU_AFFINITY_MAX_TAGS)
#   0 matching → ×1.00 | 1 → ×1.35 | 2 → ×1.70 | 3+ → ×2.05
# Raising K = more personalization; lowering it = more subtle. Without tags
# in the query (cold start) → ×1.0: the global top stays intact.
FORYOU_AFFINITY_K = 0.35
FORYOU_AFFINITY_MAX_TAGS = 3

# Regional boost (STEP 4).
# momentum_final = momentum_base × 1.5 if post.region == reader's region.
# DYNAMIC, at query time: the precomputed momentum_score (base) is NEVER
# modified — each reader sees THEIR ranking. The region is DECLARED by the
# FE (?region=, derived from navigator.language) and the BE only sanitizes
# it — NO GeoIP/IP: it is a feed preference, not a security boundary.
FORYOU_REGION_BOOST = 1.5

# Fields of the For You id-first sub-queries.
FORYOU_ROW_FIELDS = ("id", "momentum_score", "create_at", "region")

# Close You (near me, ~10 km).
# SAME ENGINE as For You (threshold, grace, precomputed momentum, POST
# pagination, serializer — see __feed_querysets/__serve_feed_page); only
# the FILTER (geohash cells instead of tags) and the BOOST (proximity
# instead of affinity+region) change:
#   momentum_final = momentum_base × proximity_boost
# The reader sends its geohash cell (precision 5, fuzzed) + 8 neighbors in
# the POST BODY — EPHEMERAL: they are never persisted (there is no map of
# people's locations). Only POSTS store a cell (author opt-in).
# DECREASING proximity boost per ring:
#   boost = 1 + K × (1 − ring/n_rings)
#   → reader's cell (ring 0): ×1.35 | edge of radius: ×1.0
# "The closest rises" holds even when the radius is 100 km.
CLOSEYOU_PROXIMITY_K = 0.35
# Feed radius: the reader expands it at will (clamp 1-100, default 15).
CLOSEYOU_RADIUS_DEFAULT_KM = 15.0
CLOSEYOU_RADIUS_MIN_KM = 1.0
CLOSEYOU_RADIUS_MAX_KM = 100.0
CLOSEYOU_ROW_FIELDS = ("id", "momentum_score", "create_at", "geohash")
