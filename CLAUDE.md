# CLAUDE.md — Thiup Backend

Source of truth for AI models and contributors working on this repo. It documents what the code
**actually does** (verified against the source). If you change behavior, update this file.

## Project overview

Thiup is an anonymous, privacy-first social network. Users have no accounts: each visitor gets a
pseudonymous **Mask** (SHA-256 of their IP) and posts short **Threads** (with media, tags,
reactions and nested replies). The backend is Django 4.x + DRF + PostgreSQL, deployed with Docker
on a **Raspberry Pi** — every design decision trades features for privacy and low resource usage.

- Repo layout: one Django project (`core/`), one main app (`app/`), plus `honeypot/` (decoy admin).
- Branch for PRs: `develop`.
- Frontend is a separate React app (out of scope here); it shares `GATEWAY_SEED` and the transport
  flags via `GET /config/`.

## Architecture

### Apps

| Package | Purpose |
|---|---|
| `core/` | Settings, root URLconf, WSGI/ASGI |
| `app/` | Everything domain: models, REST views (`app/rest/`), serializers, middlewares, crypto, management commands, tasks, tests |
| `honeypot/` | Decoy `/admin/` login that logs attackers and blacklists IPs |

### Models (`app/models/`)

All models inherit `BaseModel` (`app/models/base_model.py`): UUID `id` (internal), short
`uid` CharField (12 chars, indexed, **the public identifier** used in URLs/serializers),
`is_active`, `create_at`, `update_at`, and a `disable()` soft-delete helper.

| Model | File | Key fields / notes |
|---|---|---|
| `Thread` | `thread.py` | `text`, `text_norm` (derived: lowercase + accent-stripped, GIN pg_trgm indexed — the ONLY searched column), `content` (JSON), `sub` (self-FK: replies), `mask` FK, `visibility`, `expire_date`, `language` (lowercase, indexed, For You hard filter), `region` (uppercase, For You boost), `geohash` (12 chars, opt-in, client-fuzzed, ~5 km) + derived `geohash4` (~39 km, indexed, Close You wide filter), precomputed `momentum_score`, `unique_reactors_count`, `unique_commenters_count` (all indexed). `save()` normalizes language/region, derives `geohash4` and `text_norm`. |
| `Tag` | `tag.py` | FK to Thread; `name` + `name_norm` (GIN pg_trgm indexed for infix/prefix search) |
| `Reaction` | `reaction.py` | Catalog: unique `name` + `emoji` (seeded from `app/fixtures/reactions.json`) |
| `ReactionRelation` | `reaction_relation.py` | (thread, mask, reaction) — a user's reaction to a thread |
| `Mask` | `mask.py` | `hash` (SHA-256 of IP, unique; first 6 hex chars are the public `@id`), `country_code` (GeoIP), `unread_notifications_count` (denormalized, `F()`-updated by the notification signals) |
| `ThreadFile` | `media.py` | Media attached to a thread (file, width/height, `is_video`, `target_color`). Formats: mp4/png/jpg/jpeg |
| `MomentumLog` | `momentum_log.py` | Audit row per momentum recompute run (counts, duration, errors) |
| `PurgeLog` | `purge_log.py` | Audit row per garbage-collector run (rows selected/deleted, storage objects removed, per-model breakdown JSON, duration, errors). Admin: view/delete only |
| `SystemMetrics` | `system_metrics.py` | PROXY model (no table) — gives the admin an entry for the owner metrics dashboard (`templates/admin/system_metrics.html`, data from `app/methods/metrics.py::collect_metrics`, computed on demand: online-now via presence, worker RSS + system memory from `/proc`, DB latency/size, content counters, last momentum/GC runs) |
| `TrendingTag` | `trending_tag.py` | Precomputed trending tags (name, score = Σ momentum of carrying threads). Fully rewritten on each momentum run; `/search/suggest/` only reads it |
| `Notification` | `notification.py` | NOT a `BaseModel` (own `id`/`create_at`, no `uid`/`is_active`): `recipient`/`actor` FKs `Mask`, `verb` (`reaction`/`reply`/`profile_view`/`qr_generate`/`download`/`share`/`link_copy`), `thread` (deep-link target — nullable, null only for `profile_view`, whose target is `actor` itself), `reaction` FK (nullable), `is_read`. Created by `app/signals/notification_signals.py` (`reaction`/`reply`) or `app/rest/batch.py::_notify_engagement` (the other 5, on batch flush); also registered in the admin (add/edit works — the badge-bump signal fires for admin-created rows too, see "In-app notifications" below), hard-deleted by age in `purge_inactive` |
| `EngagementDaily` | `engagement_daily.py` | NOT a `BaseModel`: `target_type`/`target_uid` (raw uid, not FK — existence unvalidated by design), `day`, five counters (`view/qr/link_copy/download/share_count`). Upserted in bulk by `POST /batch/`, unique per `(target_type, target_uid, day)`, hard-deleted by age in `purge_inactive` |
| `BlockedTerm` | `blocked_term.py` | Moderation blocklist (shadowban filter): `term` + derived `term_norm` (indexed). Seeded by migration `0020` (CSAM / extremism / hate terms); managed from the admin. See "Shadowban blocklist" below |
| `LoginAttempt`, `BlackList` | `honeypot/models/` | Honeypot forensics; a post_save signal blacklists an IP after `HONEYPOT_LOGIN_TRYOUT` (default 5) attempts |

Key indexes: `thread_momentum_desc_idx` (`-momentum_score, -create_at`, serves For You ordering),
`thread_create_at_desc_idx`, GIN trigram indexes on `Thread.text_norm` and `Tag.name_norm`
(migration `0012`). Migration `0001` enables the `unaccent` extension; `0011` enables `pg_trgm`.

### Cross-cutting systems (and why they exist)

**E2E payload encryption** — privacy/obfuscation layer on top of TLS, toggled by
`ENCRYPTED_RESPONSE`:
- Responses: `EncodeRenderer` (`app/renders/encoder.py`) encrypts the JSON body with AES-128-CBC +
  PKCS7 (`app/cripto/kdf.py`, fresh random key/IV per response), body is reversed base64
  ciphertext, header `X-Response-Payload` carries reversed base64 `key:iv`. Wired globally as the
  DRF default renderer when the flag is on (plain `JSONRenderer` when off).
- Requests: `RequestDecryptMiddleware` (`app/middlewares/request_crypto.py`) decrypts
  POST/PUT/PATCH bodies (header `X-Request-Payload` + reversed base64 body) and hands plain JSON
  to views. When the flag is on, an unencrypted body on a protected write → 400. Exempt:
  `/ticket/`, `/config/`, `/health/`.
- The string reversal is obfuscation, not cryptography. **Never modify this layer — only use it.**

**Rotating-path gateway** (`app/rest/gateway.py`, `app/methods/gateway_path.py`) — when
`ENCRYPTED_RESPONSE=True`, all client traffic goes through `POST /{gw_hash}/` where
`gw_hash = HMAC-SHA256(GATEWAY_SEED, nonce)[:24]`, recomputed per request from the nonce inside
the envelope `{method, path, query, body, nonce}` and compared in constant time. The view
re-resolves the inner path with `django.urls.resolve()` and dispatches **only to views in
`app.rest`** (anti-SSRF allowlist; admin/honeypot/swagger/recursion blocked). `GATEWAY_SEED` is
shared with the frontend bundle — it is noise/obfuscation, **not** an auth secret. When the flag
is off the gateway returns 404 `code=GATEWAY_DISABLED` and clients call endpoints directly.

**SINGLE_REQUEST_PROTECT** — anti-replay/anti-scripting, independent from the gateway. When the
flag is on, `IsClientAuthenticated` (`app/permissions/client.py`) requires a `Client-assertion`
header carrying a short-lived JWT (HS256, signed with `API_SECRET_KEY`, TTL 300 s, payload
`{jti, iat, exp}`) issued by `GET /ticket/` (`app/methods/tokens.py`). When off, the permission
is a no-op.

**Captcha (Cap) — human pass** — anti-bot gate on entity-creating writes, flag `CAPTCHA_PROTECT`
(default off → the whole layer is a no-op and the API behaves exactly as before):
- Architecture: the FE widget (`@cap.js/widget`, invisible mode) solves a SHA-256 proof-of-work
  directly against a **Cap standalone server** (https://github.com/tiagozip/cap). Locally it is
  the `cap` + `valkey` docker-compose services (Valkey is Cap's OWN store — Django never touches
  it; the "no Redis" rule is about Django machinery). Locally `CAP_PORT` (default 3333) is the
  single knob: the container binds it and settings.py derives both Cap URLs from it. In prod the
  stack template ships cap (+ socat TLS-proxy when a managed TLS store is set) as sidecars of the
  web task — always present; `CAPTCHA_PROTECT` in SSM is the only switch (runbook in
  `ci/infra/README.md`); the explicit overrides
  `CAP_SITEVERIFY_URL`/`CAP_PUBLIC_URL` (plus `CAP_SITE_KEY`/`CAP_SECRET`) let Cap live anywhere.
  Cap's store is a self-hosted `valkey` in BOTH environments: locally the compose service
  (plain Redis over the compose network; `REDIS_URL` overrides it), in prod a task sidecar
  whose `/data` rides on EFS so site keys survive deploys (no external store, no credentials).
  Django never serves challenges and never touches this store.
- Flow: the FE exchanges Cap's single-use token ONCE at `POST /captcha/verify/`
  (`app/rest/captcha.py`) → Django redeems it against Cap's `/siteverify`
  (`app/methods/captcha.py::verify_captcha_token`, 3 s timeout, no retries) → issues a
  **human pass**: a JWT signed with `API_SECRET_KEY` (reuses `encode_token`), TTL
  `CAPTCHA_PASS_TTL` (default 600 s), payload `{jti, iat, exp, purpose: "human_pass",
  mask: <hash>}` — **bound to the requester's mask**, so it is not shareable across IPs.
- Enforcement: the `@human_validator` decorator (`app/permissions/captcha.py`) on the write
  actions (threads/reactions/reports `create`, thread-files `presign`/`confirm`) validates the
  `X-Human-Pass` header **locally** (signature + exp + purpose + mask — zero HTTP on the write
  path). Deliberately a per-action decorator: removing the line unprotects that single endpoint.
  `foryou`/`closeyou` (read-only POSTs) and all GETs are NOT decorated.
- Failure semantics: missing/expired/foreign pass → 403 `{code: "CAPTCHA_FAILED"}` (FE renews in
  the background and retries); Cap unreachable/5xx → 503 `{code: "CAPTCHA_UNAVAILABLE"}`
  **fail-closed, renewals only** — already-issued passes keep working until they expire; flag
  off → `/captcha/verify/` answers 404 `{code: "CAPTCHA_DISABLED"}` (gateway pattern).
- Abuse companion: create-only per-IP throttles (`get_throttles()` overrides, scopes
  `threads_create` 10/min, `reactions_create` 60/min, `captcha` 20/min — env-tunable) bound what
  a bot achieves within one pass window. Reads and the feed POSTs are never throttled by these.
- `/config/` additionally exposes `captcha_protect` and `captcha_endpoint` (the composed
  `{CAP_PUBLIC_URL}/{CAP_SITE_KEY}/` for the widget's `data-cap-api-endpoint`; `null` when off).
- Privacy: Cap tokens, passes and `CAP_SECRET` are never logged or echoed. The pass carries only
  the mask hash the server already knows. CORS allows the `x-human-pass` header.
- Boot guard: `CAPTCHA_PROTECT=True` without the three CAP_* coordinates →
  `ImproperlyConfigured`.

**Mask identity** — `MaskMiddleware` (`app/middlewares/mask.py`) sets `request.mask` on every
request: SHA-256(client IP) → get-or-create `Mask`, country resolved via the local
`geolite2-country.mmdb` GeoIP DB. `/health/` is exempt so it can answer when the DB is down.
Two perf contracts here: the Mask instance is CACHED in LocMem for 60 s (`mask:<hash>`), so
only a cache miss (or a country change) hits the DB — before, every request paid a
get_or_create round trip; and the GeoIP reader is a module-level SINGLETON
(`app/methods/location.py`) — it used to reopen the 68 MB .mmdb per request. It reads the file
with raw `maxminddb` (NOT the geoip2 wrapper, dropped: it dragged in aiohttp only for a
web-service client this app never calls); `.get()` works with any edition, so swapping the file
for the ~9 MB GeoLite2-Country needs no code change (recommended; the current file is actually
a City DB).

**The 60 s Mask cache assumes the row is immutable except `country_code`** — it is a WHOLE-INSTANCE
cache (`cache.set(f"mask:{hash}", obj, 60)`), not a field-level one. `Mask.unread_notifications_count`
breaks that assumption: it's mutated from a DIFFERENT request than the one reading it (the actor's
write vs. the recipient's poll), so a stale cached instance would silently serve the pre-write count
for up to 60 s with no error and no DB inconsistency — it looks exactly like "the badge just didn't
update," not a crash, which makes it easy to miss. Every writer of a mutable Mask field OTHER than
`country_code` (today: `app/signals/notification_signals.py`'s `_notify()`, and
`NotificationsViewSet.mark_read`) MUST call `invalidate_mask_cache(hash)`
(`app/middlewares/mask.py`) right after the write. Adding another mutable field to `Mask` later?
Audit every write site for this same call, or read it live instead of trusting `request.mask`.

**Presence ("online now")** — `app/methods/presence.py`, ephemeral by design:
- Passive marking: `MaskMiddleware` refreshes `online:<mask_hash>` in the LocMem cache (TTL 60 s)
  on every request — the user's own browsing IS the heartbeat; there is no heartbeat endpoint.
  `/health/` never marks (it's exempt from the middleware).
- Read side: `mask.is_online` (boolean) is ADDED to every mask payload — thread/reply authors
  (`MaskSerializer`) and the hover card (`MaskProfileSerializer`). Each read is a LocMem dict
  lookup (~1 µs); with LocMem there is no N+1 to batch (`get_many` is internally a loop).
- Privacy: boolean only, never persisted, never logged, no "last seen" timestamps — expiry IS
  the offline transition. Do NOT move this to Redis (cost anti-goal) or a DB column (trackable
  history). LocMem is per-process: exact with 1 gunicorn worker; degrades gracefully with more.

**Momentum (For You ranking)** — precomputed, never per-request:
- Formula (`app/management/commands/recompute_momentum.py`), freshness-first,
  engagement-modulated: freshness is the ONLY age-based term (a brand-new post ranks on
  arrival, no engagement required); engagement multiplies on top, log-scaled (diminishing
  returns) so it can never let an old, heavily-engaged post fully bury a brand-new one:
  `points = unique_reactors + unique_commenters×3 + commenters_replied_by_author×5`;
  `freshness = e^(-age_hours / MOMENTUM_FRESH_TAU_HOURS)` (TAU=8h);
  `engagement_boost = MOMENTUM_ENGAGE_K × ln(1 + points)` (K=0.6);
  `momentum_score = freshness × (1 + engagement_boost)`.
  Golden rule: every signal counts **distinct masks** and **excludes the author**.
- Ranking also applies a small per-request **jitter** (`foryou_jitter()` in
  `app/rest/threads.py`, ±`FORYOU_JITTER_RANGE`=0.15) — transient, re-rolled on every serve,
  never persisted, never part of the exposed `momentum_final`. Without it the sort is fully
  deterministic and shows the identical order on every refresh between recompute runs.
- Trigger: an **external scheduler runs the management command every 10 min** — there is **no
  Celery, no RabbitMQ, no in-process scheduler** (no APScheduler/threading). Locally it's the
  `momentum` service in `docker-compose.yml` (a tiny `while true; recompute_momentum; sleep 600`
  loop); in prod it's **AWS EventBridge Scheduler → an ephemeral Fargate task** running
  `python manage.py recompute_momentum`, which starts, computes, exits. Manually:
  `make recompute_momentum`.
- The logic is a standalone management command: bulk reads + batched `bulk_update`, skips
  unchanged rows, configurable window (default 30 days). Each run also rewrites the `TrendingTag`
  table (top tags by momentum sum) and logs a `MomentumLog` row. It carries all the logic and
  needs nothing else to run.

**Garbage collector (`purge_inactive`)** — hard-deletes soft-deleted rows, precomputed-style:
- `app/management/commands/purge_inactive.py`: rows with `is_active=False` whose `update_at` is
  older than `--min-age-hours` (default 24 — protects presigned uploads, which are *created*
  inactive until confirm), oldest first, capped at `--limit` (default 1000) per run; cascades
  don't count against the cap.
- **Opt-in registry** (`PURGE_MODELS`): Thread, ThreadFile, ReactionRelation, Tag, Report.
  Deliberately excluded: Mask (CASCADE would nuke its content), Reaction catalog, MomentumLog /
  TrendingTag (owned by momentum), honeypot tables (deleting un-blacklists). Add models to the
  registry consciously, never by introspection.
- Deleting a `ThreadFile` also deletes its object from storage (R2/local) via a `post_delete`
  signal → `StorageBackend.delete_object`; covers instance, bulk and cascade deletes. Storage
  failures log a warning and never block the row delete. Soft-delete (`disable()`) keeps the
  binary. Signals live in the **`app/signals/` package** (one file per domain, star-imported by
  its `__init__`, connected via `MainAppConfig.ready()`) — add new receivers there, never inline
  in models/views.
- **I/O-frugal by contract** (tested with `assertNumQueries`): ONE pk-select + ONE delete per
  registry model (cascades expand in bulk inside Django, reply subtrees walked per depth, not
  per row), doomed `file_key`s collected up front, and ALL storage objects removed in **one
  batched `DeleteObjects` request** (S3 API, ≤1000 keys — matching the run cap). During the run
  the per-row signal is paused (`storage_cleanup_paused()` in `app/signals/media_signals.py`)
  so rows fast-delete; ad-hoc deletes keep the per-row signal.
- Every run writes a `PurgeLog` row (success metrics + per-model breakdown, or the error —
  re-raised so the scheduler sees the failure). Read-only in the admin, like `MomentumLog`.
- Trigger: EventBridge Scheduler **every 2 days** (same ephemeral-Fargate pattern as momentum,
  sidecar neutralized). Locally: the `purge` docker-compose service (2-day sleep loop, mirrors
  the `momentum` service). Manually: `make purge_inactive`.
- **Retention sweeps (`Notification` / `EngagementDaily`)**: after the `PURGE_MODELS` loop, the
  same run also hard-deletes `Notification` rows older than `NOTIFICATION_RETENTION_DAYS`
  (default 30) and `EngagementDaily` rows older than `ENGAGEMENT_RETENTION_DAYS` (default 90) —
  two flat, unconditional, single-statement deletes. Deliberately **outside** `PURGE_MODELS`:
  neither model is ever soft-deleted by user action, so the `is_active=False` registry loop never
  applies to them; these are pure age-based sweeps bolted onto the existing every-2-days cron so
  no new scheduled job is needed. Counts fold into the same `PurgeLog.breakdown`, kept out of
  `total_selected`/`--limit` bookkeeping (unrelated to the cascade/storage machinery above).

**In-app notifications** — reaction, reply, and 5 engagement-derived verbs, delivered via a
paginated inbox + a denormalized unread badge, never computed per-request:
- `Notification` (`app/models/notification.py`) — NOT a `BaseModel`: never addressed by its own
  URL (the FE deep-links via `thread.uid`, or for `profile_view`, `actor`'s mask id instead) and
  never individually soft-deleted, so the UUID `id`/`uid` pair would be pure overhead on the
  app's highest-frequency insert. Fields: `recipient`, `actor` (both FK `Mask`), `verb`
  (`reaction`/`reply`/`profile_view`/`qr_generate`/`download`/`share`/`link_copy`, see
  `Notification.THREAD_VERBS`), `thread` — the DEEP-LINK TARGET for every verb EXCEPT
  `profile_view`: for `reaction` the reacted-to thread (the recipient's own content); for `reply`
  the NEW reply thread the actor created, whose own `sub` FK already points back to the
  recipient's content — so the FE reuses the existing `/threads/<uid>/responses/` permalink+
  parents logic, no extra field needed; for `qr_generate`/`download`/`share`/`link_copy` the
  thread the actor generated a QR for / downloaded the QR image of / shared / copied the link of.
  Nullable ONLY for `profile_view`, whose target isn't a thread at all — `actor` already IS that
  target (the mask who viewed the recipient's profile), so the FE routes to `/m/<actor.hash[:6]>`
  instead of a thread permalink when `thread` is null. `reaction` (FK `Reaction`, set only for the
  `reaction` verb), `is_read`.
- Created by two signal receivers (`app/signals/notification_signals.py`, same one-file-per-
  domain package as media/moderation signals): `post_save(ReactionRelation)` and
  `post_save(Thread)` (guarded on `sub_id is not None`, i.e. a reply), both skipping self-
  notifications (recipient == actor). Neither writes the badge itself — they only call
  `Notification.objects.create(...)`. The badge bump lives on a THIRD receiver,
  `post_save(Notification)` (`bump_unread_badge`), so it fires identically no matter what
  created the row: the two signals above, the Django admin's add form, or any future code path
  — creating one by hand in the admin behaves exactly like an organic one. That receiver writes
  in exactly two statements — `Mask.unread_notifications_count` atomically `F()`-incremented (no
  read-before-write), then `invalidate_mask_cache()` on the recipient's hash (see "Mask identity"
  above: the write usually happens on the ACTOR's request, not the recipient's, so the
  recipient's already-cached `Mask` instance must be dropped or it keeps serving the
  pre-increment count for up to 60 s). Zero extra queries in the two upstream signals:
  `instance.thread`/`instance.sub` are already-resolved Python instances at creation time
  (assigned via the serializers' `SlugRelatedField`/`PrimaryKeyRelatedField`, not re-fetched by
  id), so accessing `.mask_id` on them never re-hits the DB.
  `ReactionRelationSerializer.create()` already returns `None` without calling `.create()` on a
  toggle-OFF (same emoji again), so no `post_save(created=True)` fires then — no extra guard
  needed. Switching to a **different** emoji deletes-then-creates, so it intentionally DOES
  notify again (bounded by the existing `reactions_create` throttle).
- The other 5 verbs (`profile_view`/`qr_generate`/`download`/`share`/`link_copy`) are created from
  `POST /batch/` (`BatchView._notify_engagement`, `app/rest/batch.py`), NOT a `post_save` signal —
  see "Engagement batching" below for why and for the query-cost tradeoff this introduces. A
  deliberate, accepted departure from `EngagementDaily`'s "anonymous aggregate only" design: these
  5 event types are considered deliberate-enough actions to be worth surfacing with actor
  identity, unlike a passive thread `view` (which stays purely anonymous — nothing tracks that
  event as notify-eligible). `profile_view` additionally has a **24 h cooldown per (actor,
  recipient) pair** (`PROFILE_VIEW_COOLDOWN_HOURS`, served by the
  `notif_actor_recipient_verb_idx` index) — unlike a reaction or reply, a profile view is passive
  and high-volume, so without a cooldown anyone who revisits your profile repeatedly in a day
  would flood your inbox; the other 4 verbs get no cooldown (deliberate, lower-volume actions).
- Read side (`app/rest/notifications.py`, `GET/POST /notifications/...`): `unread_count` is a
  zero-query read off `request.mask` (the denormalized counter, not a `COUNT(*)`).
  `mark-read` bulk-flips every unread row for the recipient and resets the counter in two
  statements. The list groups **consecutive** same-`(thread, verb)` rows on the already-fetched
  page into one display entry (`app/methods/notifications.py::group_notifications`) — pure
  Python over the page (size 25), no extra queries, same post-fetch-enrichment spirit as
  `attach_top_replies` but simpler (nothing left to fetch). `dismiss` deletes a whole grouped
  entry at once, identified by `{thread_uid, verb}` (never the row's internal pk — grouping
  already collapsed however many rows into one displayed entry, so a dismiss removes all of
  them). `thread_uid` is omitted for `profile_view` (its rows carry no thread) — `dismiss` then
  scopes the delete with `thread__isnull=True` instead, so it can never accidentally sweep a
  thread-linked row; `DismissNotificationSerializer` rejects the opposite mismatches too
  (`thread_uid` present for `profile_view`, or missing for a `THREAD_VERBS` entry). No
  `@human_validator` on any action (read-only or non-content-creating, same reasoning as
  `foryou`/`closeyou`).

**Engagement batching (`EngagementDaily`)** — foundation of a future analytics system: a generic,
extensible daily rollup fed by client-batched, pre-aggregated telemetry (profile views, QR
generations, downloads, shares):
- `EngagementDaily` (`app/models/engagement_daily.py`) — also not a `BaseModel`, same frugality
  reasoning as `Notification`. `target_type` (`thread`/`mask`) + `target_uid` — a RAW uid, not a
  FK: the batch upsert never needs a join/lookup, and **target existence is deliberately never
  validated on write** — these are accepted as best-effort/approximate stats, like every major
  platform's view count. For `target_type="mask"` this stores the 6-hex public mask id (the same
  identifier used everywhere a mask is publicly referenced), never the full hash. `day` is always
  server-computed (`timezone.now().date()`), never trusted from the client — it's what the
  `UniqueConstraint(target_type, target_uid, day)` upserts on. Five counters: `view_count`,
  `qr_count`, `link_copy_count`, `download_count`, `share_count`.
- `POST /batch/` (`app/rest/batch.py`) writes the **whole batch in ONE multi-row
  `INSERT ... ON CONFLICT (target_type, target_uid, day) DO UPDATE SET col = table.col +
  EXCLUDED.col` statement**, via a raw `connection.cursor()` (style matches the only other raw-
  SQL usage in the codebase, `app/methods/metrics.py`) — this is what keeps the write O(1)
  queries regardless of batch size (tested with `assertNumQueries`, 1 vs 200 events). Events for
  the SAME `(target_type, target_uid)` are merged server-side into one VALUES row before the
  query, because **Postgres forbids `ON CONFLICT DO UPDATE` from touching the same conflict-key
  row twice within one statement**. Django's ORM-native `bulk_create(update_conflicts=True)` was
  considered and rejected: it does `SET col = EXCLUDED.col` (last-write-wins overwrite), not the
  additive increment this needs — it would silently drop counts on a second same-day batch.
  Request is capped at `BATCH_MAX_EVENTS` (200) events and `BATCH_MAX_EVENT_COUNT` (1000) per
  event (`app/constants/engagement.py`) — sanity caps, not real limits, since the FE already
  pre-aggregates client-side before flushing. No `@human_validator` (fire-and-forget telemetry
  the FE flushes opportunistically on an interval tick or tab-hide, not content creation — same
  reasoning as `foryou`/`closeyou`), throttle scope `batch_ingest` (30/min).
- Privacy note: this is the first place the backend receives real interaction telemetry (profile
  views, QR/download/share events) — a deliberate product decision, distinct from the frontend's
  `affinity.js` tag-personalization profile, which still never leaves the device.
- **A second step, `_notify_engagement`, runs right after the upsert and creates real
  actor-identified `Notification` rows** for `qr`/`download`/`share`/`link_copy` on a `thread`
  target and `view` on a `mask` target — see "In-app notifications" above for the per-verb
  reasoning and the `profile_view` cooldown. This breaks the upsert's own "O(1) queries
  regardless of batch size" guarantee: unlike the upsert (which never validates target
  existence), this step DOES resolve real `Thread`/`Mask` rows (so a notification always points
  at something real) and calls `Notification.objects.create()` per eligible row, not
  `bulk_create` — the `post_save` signal that bumps the recipient's badge must fire per row. To
  keep this cheap, `_notify_engagement` first filters down to only the targets whose merged
  counts actually touch a notify-eligible column **before** running any query — a batch made
  entirely of `view` events (the overwhelmingly common case: every thread/profile page load)
  still costs the upsert's original 1 query, paying nothing extra. In the eligible case it costs
  O(distinct notify-eligible targets in the batch), which stays tiny in practice: one browser tab
  reflects ~75 s of one person's activity, so it only ever touches the handful of
  threads/profiles they were actually looking at.

**Async stack reality check (cost-relevant)**: there is **no Redis, no Celery and no RabbitMQ** —
they were removed. Momentum and the every-2-days garbage collector are the only background jobs,
and they run as *ephemeral* scheduled tasks (see above), not on permanent workers. This deliberately avoids the ~750 MB of always-on
memory (Celery worker+beat ≈ 540–580 MB + RabbitMQ ≈ 184 MB) that a broker/worker would cost to
run a job that takes seconds every 10 min, vs ~50 MB (PSS) for the whole web app. **Don't
reintroduce always-on async machinery** (broker/worker/Redis); if a new background job appears,
make it another ephemeral scheduled command.

**Honeypot** — the blacklist check is served from a LocMem-cached IP set (60 s TTL,
`honeypot/middleware.py::blacklisted_ips`) instead of a per-request exists() query; post_save/
post_delete signals on `BlackList` invalidate the key, so banning/unbanning stays immediate.
The literal `/admin/` path is a fake login (`honeypot/`) that records credentials,
IP and user-agent; ≥5 attempts from one IP → `BlackList` → `HoneyPotMiddleware` returns 403 for
that IP. The real Django admin lives at the env-configured `INTERNAL_ADMIN_URL` (required at
boot, must NOT be `admin/`; the app refuses to start otherwise).

Custom middleware order (after Django's stack): `RequestDecryptMiddleware` → `MaskMiddleware` →
`HoneyPotMiddleware`. (`app/middlewares/delay.py` exists but is not registered.)

## API endpoints

All routes live in `app/rest/urls.py` (DRF `DefaultRouter` at root) and `core/urls.py`.
Default pagination: `PageNumberPagination`, page size 25.

Legend — **Enc**: response encrypted when `ENCRYPTED_RESPONSE=True` (global renderer).
**Ticket**: requires `Client-assertion` JWT when `SINGLE_REQUEST_PROTECT=True`
(`IsClientAuthenticated`). **GW**: reachable through the gateway envelope.
**Pass**: requires the `X-Human-Pass` captcha JWT when `CAPTCHA_PROTECT=True`
(`@human_validator` — see "Captcha (Cap) — human pass" above).

| Method | Path | View (file in `app/rest/`) | Purpose | Enc | Ticket | Pass | GW |
|---|---|---|---|---|---|---|---|
| GET | `/config/` | `ConfigView` (`config.py`) | Transport flags: `{encrypted_response, single_request_protect, captcha_protect, captcha_endpoint}` | yes | no (AllowAny) | no | no (bootstrap) |
| GET | `/ticket/` | `TicketView` (`ticket.py`) | Issues client-assertion JWT (anon throttle 120/min) | yes | no (AllowAny) | no | no (bootstrap) |
| GET | `/health/` | `HealthCheckView` (`health.py`) | Liveness probe → 200 `{"status":"ok"}` (no DB) | yes | no (AllowAny — the ECS container health check can't send a ticket) | no | yes |
| GET | `/me/` | `CurrentMaskView` (`masks.py`) | Current mask: `{mask_id, joined, country_code, stats:{threads, reactions, replies}}` | yes | yes | no | yes |
| POST | `/captcha/verify/` | `CaptchaVerifyView` (`captcha.py`) | Exchanges a single-use Cap token for the human pass (throttle 20/min); 404 `CAPTCHA_DISABLED` when the flag is off | yes | yes | no (it MINTS the pass) | yes |
| GET/POST | `/threads/` | `ThreadsViewSet` (`threads.py`) | List (`?q=`, `?tag=`) / create thread (create throttle 10/min; `text` capped at `THREAD_TEXT_MAX_LENGTH` = 500 — Threads' exact limit, posts and replies alike, input-only) | yes | yes | POST only | yes |
| GET | `/threads/<uid>/` | 〃 | Thread detail | yes | yes | no | yes |
| GET | `/threads/<uid>/responses/` | 〃 | Replies of a thread. The uid can be a REPLY at any depth (comment permalink): `head` is then that reply and the context carries `parents` — its ancestor chain (root first, immediate parent last), truncated at the first hidden/expired ancestor. `op_mask`/`is_op` are computed against the chain ROOT's author | yes | yes | no | yes |
| GET | `/threads/mine/` | 〃 | Threads of `request.mask` | yes | yes | no | yes |
| POST | `/threads/foryou/` | 〃 | For You feed; ephemeral body `{lang, region, tags}`. Cards may carry `top_reply` — ONE direct reply per root (X-style conversation preview, `app/methods/threads.py::attach_top_replies`), decided by two rules IN CASCADE: (A) SELF-THREAD priority — the root author continued their own thread (mask equality, boolean-only privacy) → their FIRST chronological continuation shows with NO engagement threshold (the author's thread never competes); (B) EARNED fallback — the best third-party reply passing BOTH gates: ≥ `FEED_TOP_REPLY_MIN_REACTORS` unique reactors excluding its own author (absolute floor) AND ≥ `FEED_TOP_REPLY_ROOT_RATIO` × the root's precomputed `unique_reactors_count` (relative bar — self-calibrating scarcity at scale; a 0-reactor root passes trivially). Best = most reactors, tie → newest. Shared by EVERY root-card surface: For You, Close You, `/threads/` list, `/threads/mine/` and the search posts tab — never `/responses/` (the thread view shows the real tree) | yes | yes | no (read-only POST) | yes |
| POST | `/threads/closeyou/` | 〃 | Close You feed; body `{geohash, radius_km}` (1–100, default 15) | yes | yes | no (read-only POST) | yes |
| GET/POST | `/reactions/` | `ReactionsViewSet` (`reactions.py`) | List catalog / react to a thread — toggle semantics; POST answers a minimal `{status: "ok"}` ack (the FE renders optimistically and never read the old reaction-breakdown echo) (create throttle 60/min) | yes | yes | POST only | yes |
| POST | `/reports/` | `ReportsViewSet` (`reports.py`) | Report a thread — upsert per (thread, reporter mask) (throttle 30/min) | yes | yes | yes | yes |
| POST | `/thread-files/presign/` | `ThreadFilesViewSet` (`thread_files.py`) | Step 1 of the direct-to-storage upload: issue PUT URL + detached pending `ThreadFile` | yes | yes | yes | yes |
| POST | `/thread-files/confirm/` | 〃 | Step 3: verify the object exists, attach to thread, activate | yes | yes | yes | yes |
| GET | `/search/` | `SearchViewSet` (`search.py`) | Search; `?q=&type=posts\|tags\|users\|media&ordering=&page=` (throttle 60/min) | yes | yes | no | yes |
| GET | `/search/suggest/` | 〃 | Autocomplete (throttle 240/min) | yes | yes | no | yes |
| GET | `/users/<hash>/` | `MasksViewSet` (`masks.py`) | Mask hover-card: `{mask_id, joined, posts_count, replies_count, reactions_count, is_online}` (accepts full hash or the 6-hex public id) | yes | yes | no | yes |
| GET | `/notifications/` | `NotificationsViewSet` (`notifications.py`) | Paginated inbox, grouped at read by `(thread, verb)` | yes | yes | no | yes |
| GET | `/notifications/unread-count/` | 〃 | `{unread_count}` — zero-query read off `request.mask`'s denormalized counter | yes | yes | no | yes |
| POST | `/notifications/mark-read/` | 〃 | Bulk-flips every unread row for the caller + resets the counter | yes | yes | no | yes |
| POST | `/notifications/dismiss/` | 〃 | Deletes a whole grouped entry, identified by `{thread_uid, verb}` — never an internal pk | yes | yes | no | yes |
| POST | `/batch/` | `BatchView` (`batch.py`) | Batched engagement telemetry (view/qr/link_copy/download/share) → `EngagementDaily` upsert + `Notification` creation for 5 of those event types (throttle 30/min, ≤200 events/request) | yes | yes | no | yes |
| POST | `/{gw_hash}/` (24 hex) | `GatewayView` (`gateway.py`) | Rotating gateway; dispatches the inner envelope | yes | inner view's | inner view's | — |
| any | `/admin/` | honeypot | Decoy admin; logs and blacklists | — | — | — | blocked |
| any | `/{INTERNAL_ADMIN_URL}` | Django admin | Real admin (obfuscated path) | — | staff | — | blocked |
| GET | `/{INTERNAL_ADMIN_URL}swagger/` | drf_yasg | Schema (IsAdminUser); lives under the REAL obfuscated admin path (never under the `/admin/` honeypot); only registered when `ENABLE_SWAGGER` (default: `DEBUG`) — drf_yasg costs ~10-20 MB RSS per process | — | — | — | blocked |

`TagsViewSet` is registered at `/tags/` but currently defines no actions (stub).

**Write-path field safety (mass-assignment).** `ThreadSerializer` uses `exclude`, so any model
field not listed is auto-writable. Server-controlled fields MUST stay out of client reach:
`momentum_score` is `read_only_fields` (emitted for the FE's DEBUG view, never accepted — a
writable one let a client top the feed/search ranking), and `mask` is EXCLUDED as an input
(authorship is forced from `request.mask`; `to_representation` still emits it from the instance).
When adding a model field, decide explicitly whether it is client-writable. Replies
(`POST /threads/` with `sub`) only attach to a LIVE, visible parent (`is_active=True,
visibility=True`) — mirrors the reaction/report filters.

**Thread-card contract (trimmed to what the FE reads).** The card emits: `uid`, `text`,
`create_at` (relative string), `created_at_iso`, `responses_count`, `media[]` (`uid`, `file`,
`is_video`, `is_nsfw`, `width`, `height`, `target_color` — no internal `id`), `reactions[]`
(`{id, name, emoji, reaction_count}`), `last_reaction`, `mask` (`{hash, is_online}` ONLY — no
internal UUID/uid/country_code), `is_mine`, `is_op`, `momentum_score` (+ `momentum_final` on the
feeds) and `responses[]` (only under `show_responses`; nested replies serialize through
`with_card_relations`, fast path). Deliberately REMOVED (verified unread by the FE — do not
re-add without a consumer): `content` and `sub` (now write-only create inputs), `parent`,
`is_new`, top-level `reactions_count`, `geohash4` (location metadata; privacy). Same trim
elsewhere: presign returns `{upload_url, method, headers, uid}`; confirm returns
`{uid, public_url, is_video, is_nsfw}` (the storage `key`, `file_url` dup and `metadata` echo
stay internal).

## Search & feed internals

How to extend without breaking things — the load-bearing invariants:

**Normalization contract.** `app/utils/text.py::strip_accents` (NFKD, drop combining marks) +
`.lower()` is applied at **write time** (stored in `Thread.text_norm`, `Tag.name_norm` via
`save()`) and the **same function** at query time. Queries then use plain `__contains` /
`__startswith` against the `*_norm` columns so the GIN pg_trgm indexes are used. Never query with
`__unaccent__icontains` on raw columns — `UPPER(UNACCENT(col))` can't use the index (measured:
seq scan ~41 ms vs ~2 ms on 55k threads). If you add a searchable field, add a `*_norm` twin,
derive it in `save()`, backfill in the migration, and index it.

**Search tabs** (`/search/?type=`): `posts` (root threads, `text_norm__contains`, ordered by
`-momentum_score` or `-create_at`), `tags` (grouped by name with distinct-thread counts and a
14-day activity sparkline, cached 5 min in LocMem keyed by tag-set MD5), `users` (mask public-ID
prefix match with annotated `posts_count`), `media` (Twitter-style gallery: the active
`ThreadFile`s of the threads the posts tab matches — a semi-join over the same flat posts filter,
served by the trigram index + the `thread_id` FK index — ordered by parent momentum then date,
files in upload order within a thread; each item is the thread-card media shape plus `thread`,
the parent's full card serialized once per distinct thread of the page). Every search response
includes `context.counts = {posts, tags, users, media}` — the four KEYS are frozen contract, but
only the ACTIVE tab is actually counted (the other three report 0): the FE deliberately renders
no tab badges, so the inactive COUNTs were pure per-request cost. The `users` count in
particular used to seq-scan all masks via `hash__unaccent__icontains`; the users tab now matches
`hash__startswith` (the public @id is a prefix — same rule as `_author_mask_query`).
`SearchPagination` reuses the active tab's precomputed count to avoid a duplicate COUNT query.
Query length:
max 100 chars; suggest needs ≥2 chars (below that: trending tags only, read from `TrendingTag`).
Searching `@xxxxxx` (6 hex chars) matches a mask's public ID exactly.

**Shadowban blocklist** (`BlockedTerm` + `app/methods/moderation.py`): a query carrying any
active blocked term returns exactly the no-match shape — `/search/` answers the same empty
`{counts: 0…}` contract as an empty query, `/search/suggest/` returns `{"tags": [], "threads": []}`
and `/threads/?q=|?tag=` an empty page — indistinguishable from "nobody ever posted that".
Matching is **whole-word** (Python `(?<!\w)…(?!\w)` / Postgres `\y…\y`), case- and
accent-insensitive: both the query and `term_norm` go through the same normalization contract
above, and the DB sweep prefilters with `__contains` (trigram index) before the boundary regex.
A blocked **main search** additionally soft-deletes (`is_active=False`) every thread and tag
still carrying the term (`shadowban_matching`); suggest and the threads list are read-only
(suggest fires per keystroke). Saving a `BlockedTerm` in the admin sweeps immediately via the
`post_save` signal (`app/signals/moderation_signals.py`) and invalidates the LocMem-cached list
(`moderation:blocked_terms`, TTL 5 min). **CAREFUL**: swept rows are later HARD-DELETED by the
purge GC (Thread/Tag are in `PURGE_MODELS`) — a broad term permanently removes innocent content;
keep entries specific. Blocked queries follow the privacy rule: never logged, never echoed.

**For You** (`/threads/foryou/`): candidates = global top-by-momentum ∪ posts matching the user's
tags. Entry threshold: `unique_commenters ≥ 1 OR unique_reactors ≥ 3 OR age < 2h`. Final score =
`momentum_score × region_boost × affinity_boost` where `region_boost = 1.5` if post region ==
user region, `affinity_boost = 1 + 0.35 × min(matching_tags, 3)`. `language` is a hard indexed
filter with a global fallback when empty. Personalization inputs (`lang`, `region`, `tags`) come
in the POST body, are used for that query only and are **never persisted**. Implementation is
ID-first: cheap subqueries pick page IDs, the rich queryset (prefetches) only hydrates the page.

**Close You** (`/threads/closeyou/`): filters by geohash cells (adaptive precision 4 or 5 from
`radius_km`), proximity boost decays per ring (`1 + 0.35 × (1 - ring/n_rings)`), backfills with
the newest local posts. Geohashes are client-fuzzed and opt-in — the server never stores raw
coordinates.

**Momentum changes**: keep the golden rule (distinct masks, author excluded). Constants
(`COMMENTER_WEIGHT=3`, `AUTHOR_REPLY_WEIGHT=5`, `DECAY_EXPONENT=1.5`, `AGE_SOFTENER_HOURS=2`)
live at the top of `recompute_momentum.py`. Anything expensive belongs in the cron, not the
request path; expose it as a precomputed indexed column like `momentum_score`.

## Tests

- Framework: **pytest + pytest-django** (config in `pyproject.toml`; `testpaths = ["app"]`,
  helpers live inside the test files; the only `conftest.py` (`app/tests/conftest.py`) is an
  autouse fixture that clears the LocMem cache before each test — the DB rolls back per test
  but cached state (masks, blacklist set, throttles, presence, blocklist) would otherwise leak
  across tests).
- Files: `app/tests/test_foryou.py` (momentum, For You/Close You, search, request crypto),
  `test_gateway.py` (rotation, HMAC validation, anti-SSRF, toggles), `test_thread_view.py`
  (CRUD/replies/search, uses `TransactionTestCase`), `test_reaction_view.py`, `test_captcha.py`
  (siteverify seam, human pass, `@human_validator` scope, create throttles).
- Run inside Docker: `make test` — a one-off container running pytest with coverage
  (term + HTML) and the **≥70%** gate (`--cov-fail-under=70`); no running stack required
  (the local image bakes in the dev deps). Or `docker compose exec web pytest`.
- Tests are **sensitive to `ENCRYPTED_RESPONSE` / `SINGLE_REQUEST_PROTECT`**: crypto/gateway
  tests pin the flags with `@override_settings`. When writing tests that hit the API, either
  pin the flags or use the existing helpers (`encrypted_post`, `gateway_post`, `decode_body`,
  `make_mask`, `make_thread`) from `test_foryou.py` / `test_gateway.py`.
- `CAPTCHA_PROTECT` is forced **OFF** under pytest (opposite of the transport flags) so the
  suite needs no pass headers; `test_captcha.py` pins it ON per-class and mocks the siteverify
  HTTP seam (`app.methods.captcha._session.post` / `app.rest.captcha.verify_captcha_token`).
  The create throttles DO run under pytest (LocMem-backed): a test class that fires many API
  creates from the shared client IP should `cache.clear()` in `setUp` (see `ThreadsViewTest`).
- Convention: every behavior change ships with a test. Spanish strings in test *data* are fine
  (user content); test names, comments and docstrings are English.

## Local dev / setup

```bash
cp .env.template .env   # fill in: SECRET_KEY, API_SECRET_KEY, INTERNAL_ADMIN_URL, DB creds...
make build
make up                            # the `migration` compose service applies migrations on start
make load_fixtures                 # reaction catalog (separate shell; `make up` runs in foreground)
make test
```

- Local stack (`make up`): postgres:15 (host port **5433**), `runserver` with autoreload,
  migration, momentum loop, purge loop, nginx, plus the Cap captcha standalone (`cap` on host
  port **3333** + `valkey`, its own store). Production is AWS ECS/Fargate — not docker-compose
  (see "Production infrastructure" below). Local entry point: nginx on `SERVER_PORT`
  (default 8080).
- Captcha one-time setup (only if you turn `CAPTCHA_PROTECT` on): set `CAP_ADMIN_KEY` in `.env`,
  `make up`, open the Cap dashboard at `http://localhost:3333`, create a key, paste
  `CAP_SITE_KEY`/`CAP_SECRET` into `.env`. `web` boots fine with Cap down (fail-closed).
- Useful targets (see `make help`): `make shell`, `make shell-db` (psql), `make logs-web`,
  `make add_dummy_threads`, `make recompute_momentum`, `make validate-config`,
  `make dependencies` (rebuild the web image to pick up requirements changes; the local
  image installs prod + `requirements.dev`). Edit `requirements.in`, never `requirements.txt`
  directly; regenerate the lock with `pip-compile` (`requirements.dev` adds the test tooling).
- Admin: `http://localhost:8080/{INTERNAL_ADMIN_URL}` (from your `.env`). `/admin/` is the
  honeypot — don't "fix" it.
- Python 3.12 (Docker image). `app/` directory does all the work; `media/` and `staticfiles/`
  are served by nginx via volumes.

**Lint & format (Ruff).** One tool for both, config in `pyproject.toml` `[tool.ruff]`
(line-length 120, target py312, rules `E/W/F/I`; `E501` is left to the formatter; migrations
excluded; star-imports and test locals per-file-ignored as intentional). `make lint` checks
(`ruff check` + `ruff format --check`, no writes — this is what CI should run); `make format`
rewrites (`ruff check --fix` + `ruff format`). A `.pre-commit-config.yaml` runs both on commit
(`pip install pre-commit && pre-commit install`). Ruff lives in `requirements.dev`, baked into
the local image. The whole repo was reformatted once — that commit is in `.git-blame-ignore-revs`
(enable: `git config blame.ignoreRevsFile .git-blame-ignore-revs`).

## Production infrastructure (AWS)

One CloudFormation stack (`ci/infra/ecs.yml`) owns everything: ECS Fargate service + CodeBuild
CI + the EventBridge momentum schedule. Full detail and runbooks: `ci/infra/README.md`. The
load-bearing facts:

- **Ingress is a Cloudflare Tunnel, not a load balancer.** A `cloudflared` sidecar in the web
  task opens an outbound-only connection to Cloudflare's edge and forwards to gunicorn over
  the task-local loopback (`localhost:8000`). There is **no ALB and no inbound security-group
  rule** — the task's public IP is egress-only (ECR/DB/Cloudflare). The hostname →
  `localhost:8000` routing lives in the Cloudflare Zero Trust dashboard; the only AWS-side
  piece is the connector token (Secrets Manager `/<stack>/TUNNEL_TOKEN`). The tunnel ID (and
  DNS) never changes across deploys — connectors self-register, rolling deploys overlap two
  connectors, zero downtime.
- **Client IP resolution is security-critical and hardened** (`app/utils/client.py::get_client_ip`).
  Mask identity, GeoIP, the honeypot blacklist AND every per-IP throttle key on it, so it must
  come from a hop the client cannot forge. The resolver: (1) prefers `CF-Connecting-IP` when
  `TRUST_CLOUDFLARE` is on (Cloudflare's edge SETS/overwrites it — unspoofable behind the tunnel,
  absent on a forged request); (2) else takes the X-Forwarded-For entry the outermost trusted
  proxy APPENDED (`TRUSTED_PROXY_COUNT` from the right, nginx=1), NOT the leftmost — the client
  can only forge entries to the left; (3) else `REMOTE_ADDR`. `TRUST_CLOUDFLARE` defaults to
  `not DEBUG`. **The old code trusted the leftmost XFF entry, which was fully client-forgeable**
  (nginx `$proxy_add_x_forwarded_for` and Cloudflare both APPEND) → mask impersonation, throttle
  evasion and blacklist poisoning; that is now fixed. DRF throttles use
  `app/permissions/throttling.py` (`TrustedIP*RateThrottle`) so they key on the same trusted IP,
  not the raw XFF string.
- **Health is the container-level health check** (python urllib against
  `http://127.0.0.1:8000/health/`; the slim image has no curl). If `ALLOWED_HOSTS` is ever
  tightened from `"*"`, it MUST include `127.0.0.1` or ECS cycles the task on 400s.
- **Golden rule for one-off RunTasks** (momentum, collectstatic, migrate, or anything new):
  override the `cloudflared` container command to `["version"]` so the ephemeral task never
  registers as a live tunnel connector with no gunicorn behind it (502s). The momentum
  schedule `Input`, `ecs-deploy run_ephemeral` and `automigrate.py` already do this; copy
  that pattern. The sidecar is `Essential: false` (ephemeral tasks can exit) with a
  `RestartPolicy` (crashed connector restarts in place; exit 0 ignored).
- **Cost frugality is a design constraint** (~$17/mo total; it was $41 before the ALB was
  removed): smallest Fargate size (0.25 vCPU / 512 MB — the app runs at ~84 MB with 1
  gunicorn worker), momentum every 10 min (each tick is a billed ephemeral task — see the cost
  trade-off note in ci/infra/README.md's changelog), multi-stage
  slim image (291 MB, was 1.15 GB → 564 MB → 291 MB after trimming deps: no drf-yasg/geoip2+aiohttp/
  Pillow/Faker/geonamescache/humanize in prod, botocore pruned to s3-only, .dockerignore keeps
  .git out — Fargate bills from pull start, and momentum pays that pull
  every tick; don't add system packages to the runtime stage). Don't reintroduce a load
  balancer, NAT gateway, or always-on machinery; cost knobs and history are in
  `ci/infra/README.md`.

## Constraints

- **Raspberry Pi frugality**: ~1–2 gunicorn gthread workers × 4 threads (prod image defaults
  to 1 on the 0.25 vCPU / 512 MB Fargate task; override with `GUNICORN_WORKERS`),
  `max_requests` recycling, `CONN_MAX_AGE=60`, sparse logging (SD card). No Redis, no Celery,
  no RabbitMQ. Expensive computation goes into the 30-min ephemeral scheduled command, never
  the request path.
- **Privacy**: search queries, feed personalization inputs and geolocation are ephemeral — never
  log or persist them, never echo them in error messages. Only **public** identifiers (`uid`,
  mask `hash` prefix) leave the API; internal UUIDs stay internal. No raw coordinates, no
  trackable history, generic cookie names (`x_t`, `x_s`), no Server headers (nginx strips them).
- **Crypto is untouchable**: `app/cripto/`, `app/renders/encoder.py`,
  `app/middlewares/request_crypto.py`, gateway token derivation. Use them; never alter them.
- **Endpoint contracts are frozen**: the React frontend depends on the exact shapes documented
  above (including `X-Response-Payload` semantics and `context.counts`). Add, don't mutate.

## RULES for AI models / contributors

1. **ENGLISH ONLY** — all code, comments, docstrings, identifiers, docs and commit messages in
   English. **Every NEW or edited comment/docstring MUST be written in English, no exceptions** —
   do not add Spanish comments. **Single exception**: Spanish i18n translation strings and Spanish
   user content are *data*, not code — never "translate" or rewrite them.
   (`ci/scripts/check_comment_language.py` flags Spanish comments; legacy Spanish comments still
   remain in some files — migrate them to English when you touch those lines, don't mass-rewrite.)
2. **Measure before optimizing.** No blind optimization — use `EXPLAIN ANALYZE`, the existing
   `MomentumLog` timings, or a reproducible benchmark first.
3. **Never touch the E2E crypto** (encoder, KDF, request-decrypt middleware, gateway token
   derivation). Consume it through the existing renderer/middleware/permission wiring.
4. **Efficiency first (Raspberry Pi)**: no Redis or new brokers; indexed queries only; no N+1
   (prefetch/annotate like the existing views); precompute expensive things via the momentum
   cron or a management command.
5. **Privacy**: never log/persist queries, feed inputs or histories; only public identifiers are
   searchable; never leak data through logs, error messages, OG tags or titles.
6. **Write/update tests** for every added or changed behavior; keep `make test` (≥70%
   gate) green; pin `ENCRYPTED_RESPONSE`/`SINGLE_REQUEST_PROTECT` in API tests. Also keep
   **`make lint` green** (Ruff — see "Lint & format" above); run `make format` before committing.
7. **Clean migrations** for any model/index change — including backfills for derived `*_norm`
   columns and index additions/removals (see migration `0012` as the model to follow).
8. **Don't break API contracts**: response shapes, headers, pagination and status codes are
   consumed by the frontend. Refactors must be behavior-preserving; prove it with tests.
9. **Discovery before modification**: read the model, view, serializer and tests involved before
   changing anything. This file is the map, not a substitute for reading the code.
10. **Clean, elegant comments — no ASCII dividers.** Never use ruler/divider comment lines
    (`# ----`, `# ====`, box headers, etc.) to separate sections. Let the code structure speak;
    when a section genuinely needs a label, use a single short comment line (`# Backend selection.`).
    Comments should explain the *why*, be concise, and stay in English (see rule 1).
11. **Never create branches or commit automatically.** Leave all changes UNCOMMITTED in the
    working tree on the current branch — do not run `git checkout -b`, `git commit`, or `git push`
    unless the user EXPLICITLY asks in that message. When work is done, stop and let the user
    review, stage and commit. Suggesting a branch name or commit message is fine; running the
    git commands is not.
