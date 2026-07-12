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
| `Mask` | `mask.py` | `hash` (SHA-256 of IP, unique; first 6 hex chars are the public `@id`), `country_code` (GeoIP) |
| `ThreadFile` | `media.py` | Media attached to a thread (file, width/height, `is_video`, `target_color`). Formats: mp4/png/jpg/jpeg |
| `MomentumLog` | `momentum_log.py` | Audit row per momentum recompute run (counts, duration, errors) |
| `PurgeLog` | `purge_log.py` | Audit row per garbage-collector run (rows selected/deleted, storage objects removed, per-model breakdown JSON, duration, errors). Admin: view/delete only |
| `SystemMetrics` | `system_metrics.py` | PROXY model (no table) — gives the admin an entry for the owner metrics dashboard (`templates/admin/system_metrics.html`, data from `app/methods/metrics.py::collect_metrics`, computed on demand: online-now via presence, worker RSS + system memory from `/proc`, DB latency/size, content counters, last momentum/GC runs) |
| `TrendingTag` | `trending_tag.py` | Precomputed trending tags (name, score = Σ momentum of carrying threads). Fully rewritten on each momentum run; `/search/suggest/` only reads it |
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
- Formula (`app/management/commands/recompute_momentum.py`):
  `points = unique_reactors + unique_commenters×3 + commenters_replied_by_author×5`;
  `momentum_score = points / (age_hours + 2)^1.5`.
  Golden rule: every signal counts **distinct masks** and **excludes the author**.
- Trigger: an **external scheduler runs the management command every 30 min** — there is **no
  Celery, no RabbitMQ, no in-process scheduler** (no APScheduler/threading). Locally it's the
  `momentum` service in `docker-compose.yml` (a tiny `while true; recompute_momentum; sleep 1800`
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

**Async stack reality check (cost-relevant)**: there is **no Redis, no Celery and no RabbitMQ** —
they were removed. Momentum and the every-2-days garbage collector are the only background jobs,
and they run as *ephemeral* scheduled tasks (see above), not on permanent workers. This deliberately avoids the ~750 MB of always-on
memory (Celery worker+beat ≈ 540–580 MB + RabbitMQ ≈ 184 MB) that a broker/worker would cost to
run a job that takes seconds every 30 min, vs ~50 MB (PSS) for the whole web app. **Don't
reintroduce always-on async machinery** (broker/worker/Redis); if a new background job appears,
make it another ephemeral scheduled command.

**Honeypot** — the literal `/admin/` path is a fake login (`honeypot/`) that records credentials,
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
| GET | `/me/` | `CurrentMaskView` (`masks.py`) | Current mask: `{mask_id, country_code}` | yes | yes | no | yes |
| POST | `/captcha/verify/` | `CaptchaVerifyView` (`captcha.py`) | Exchanges a single-use Cap token for the human pass (throttle 20/min); 404 `CAPTCHA_DISABLED` when the flag is off | yes | yes | no (it MINTS the pass) | yes |
| GET/POST | `/threads/` | `ThreadsViewSet` (`threads.py`) | List (`?q=`, `?tag=`) / create thread (create throttle 10/min) | yes | yes | POST only | yes |
| GET | `/threads/<uid>/` | 〃 | Thread detail | yes | yes | no | yes |
| GET | `/threads/<uid>/responses/` | 〃 | Replies of a thread | yes | yes | no | yes |
| GET | `/threads/mine/` | 〃 | Threads of `request.mask` | yes | yes | no | yes |
| POST | `/threads/foryou/` | 〃 | For You feed; ephemeral body `{lang, region, tags}` | yes | yes | no (read-only POST) | yes |
| POST | `/threads/closeyou/` | 〃 | Close You feed; body `{geohash, radius_km}` (1–100, default 15) | yes | yes | no (read-only POST) | yes |
| GET/POST | `/reactions/` | `ReactionsViewSet` (`reactions.py`) | List catalog / react to a thread (create throttle 60/min) | yes | yes | POST only | yes |
| POST | `/reports/` | `ReportsViewSet` (`reports.py`) | Report a thread — upsert per (thread, reporter mask) (throttle 30/min) | yes | yes | yes | yes |
| POST | `/thread-files/presign/` | `ThreadFilesViewSet` (`thread_files.py`) | Step 1 of the direct-to-storage upload: issue PUT URL + detached pending `ThreadFile` | yes | yes | yes | yes |
| POST | `/thread-files/confirm/` | 〃 | Step 3: verify the object exists, attach to thread, activate | yes | yes | yes | yes |
| GET | `/search/` | `SearchViewSet` (`search.py`) | Search; `?q=&type=posts\|tags\|users\|media&ordering=&page=` (throttle 60/min) | yes | yes | no | yes |
| GET | `/search/suggest/` | 〃 | Autocomplete (throttle 240/min) | yes | yes | no | yes |
| GET | `/users/<hash>/` | `MasksViewSet` (`masks.py`) | Mask hover-card: joined, posts/replies counts | yes | yes | no | yes |
| POST | `/{gw_hash}/` (24 hex) | `GatewayView` (`gateway.py`) | Rotating gateway; dispatches the inner envelope | yes | inner view's | inner view's | — |
| any | `/admin/` | honeypot | Decoy admin; logs and blacklists | — | — | — | blocked |
| any | `/{INTERNAL_ADMIN_URL}` | Django admin | Real admin (obfuscated path) | — | staff | — | blocked |
| GET | `/admin/swagger/` | drf_yasg | Schema (IsAdminUser) | — | — | — | blocked |

`TagsViewSet` is registered at `/tags/` but currently defines no actions (stub).

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
includes `context.counts = {posts, tags, users, media}` for the four tabs regardless of the
active one;
`SearchPagination` reuses these precomputed counts to avoid duplicate COUNT queries. Query length:
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
  no `conftest.py` — helpers live inside the test files).
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
- **Client IP arrives in `X-Forwarded-For` (set by Cloudflare, first entry);** `REMOTE_ADDR`
  is the loopback. Mask identity, honeypot blacklisting and GeoIP all rely on that first XFF
  entry (`app/middlewares/mask.py`, `app/utils/client.py`) — same value as the old ALB path,
  so masks survived the migration. With no direct path to gunicorn, XFF is not spoofable.
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
  gunicorn worker), momentum every 30 min (each tick is a billed ephemeral task), multi-stage
  slim image (564 MB vs 1.15 GB — Fargate bills from pull start, and momentum pays that pull
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
