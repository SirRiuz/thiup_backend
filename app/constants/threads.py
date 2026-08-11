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
#   unique_commenters ≥ 1  OR  unique_reactions ≥ 3  OR  age < 6h
# (the grace window gives new posts a real shot at organic discovery before
# the quality gate kicks back in — see FORYOU_GRACE_HOURS below). All
# fields are precomputed by `recompute_momentum`: the WHERE is over indexed
# counters, recomputing nothing per request.
# Thread/reply text limit — Threads' exact 500 (their posts and replies share
# one limit; a reply IS a Thread here too). Enforced server-side by the
# serializer (the FE composers mirror it with their MAX_CHARS; keep in sync).
# INPUT-only: legacy longer rows still serialize fine.
THREAD_TEXT_MAX_LENGTH = 500

FORYOU_MIN_COMMENTERS = 1
FORYOU_MIN_REACTORS = 3
# Extended from 2h to 6h alongside the freshness-first momentum redesign
# (see recompute_momentum.py): a fresh, zero-engagement post now has a real,
# nonzero momentum_score from freshness alone, so it needs a fair runway to
# actually get discovered and earn its first reactions/comments organically
# — 2h was too tight for that. Deliberately NOT stretched to cover the
# formula's whole ~24h freshness tail (see MOMENTUM_FRESH_TAU_HOURS below):
# past this gate, a post still needs the SAME real engagement bar as before
# (≥1 commenter or ≥3 reactors) to stay in the pool — otherwise every post
# from the last 24h would qualify regardless of quality, and the feed fills
# with unengaged noise. This is the quality gate; TAU below is just ranking
# among posts that already qualify.
FORYOU_GRACE_HOURS = 6

# Momentum formula (recompute_momentum.py) — LEAKY-BUCKET model ("vaso de
# agua"): a base freshness that drains from the post's own create_at, plus a
# pulse where every interaction is its OWN droplet that decays from ITS OWN
# timestamp. This is what lets a brand-new comment or reaction lift an old,
# already-drained post — the pulse ADDS on top instead of multiplying an
# already-near-zero base.
#   freshness_base = e^(-post_age_hours / MOMENTUM_FRESH_TAU_HOURS)
#   pulse = Σ e^(-event_age_hours / MOMENTUM_FRESH_TAU_HOURS) × event_weight
#   momentum_score = freshness_base + MOMENTUM_ENGAGE_K × ln(1 + pulse)
# TAU=8h → a droplet is ~37% strong at 8h, ~5% at 24h (evaporates within a
# day) — same curve as the base freshness, just re-centered on each event.
# log1p on the pulse keeps the old diminishing-returns guarantee: a pile-up
# of simultaneous interactions still can't blow one post's score out of
# proportion to everything else.
MOMENTUM_FRESH_TAU_HOURS = 8
MOMENTUM_ENGAGE_K = 0.6

# Ranking jitter (For You + Close You) — never persisted, never applied to
# the stored momentum_score nor the exposed momentum_final. Without it the
# deterministic sort shows the identical order on every refresh between
# recompute_momentum runs. DETERMINISTIC per (thread, time bucket) — not a
# fresh random draw per call — so pagination (page 1 / page 2 of the same
# browse) stays consistent within a bucket, while the order still changes
# once the bucket rolls over. See foryou_jitter() in app/rest/threads.py.
#
# The RANGE itself scales with content age: new/unproven posts get the wide
# ±FORYOU_JITTER_RANGE_MAX swing (more shuffling → more chances to surface
# and get discovered), while posts past FORYOU_JITTER_DECAY_HOURS settle to
# the narrow ±FORYOU_JITTER_RANGE_MIN (established order stays stable,
# instead of randomly reshuffling content that already found its place).
FORYOU_JITTER_RANGE_MAX = 0.15
FORYOU_JITTER_RANGE_MIN = 0.05
FORYOU_JITTER_DECAY_HOURS = 12
FORYOU_JITTER_BUCKET_SECONDS = 600  # 10 min — matches the recompute cadence

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
