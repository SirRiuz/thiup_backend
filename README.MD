Thiup
======

Freedom absolute is what internet deserves, join this comunity to express all what you want beign total anonymous.

Thiup is an open-source project aimed at creating a free social network that promotes freedom of expression, allowing all users to share their opinions anonymously. On this platform, we foster an environment where diversity of thought and viewpoints are valued and respected. We believe in the importance of providing a space where personal expression can flourish without fear of reprisal or censorship


* **Homepage:** https://www.thiup.com/
* **Code & BugTracker:** https://github.com/SirRiuz/thiup_backend
* **Front-end:** https://github.com/SirRiuz/thiup_frontend/

### Running Thiup in development without `dev`

1. `docker-compose build`: builds docker containers
2. `docker-compose up`: starts dev server

## Environments (STAGE)

The project supports two environments via the `STAGE` variable in `.env`:

| STAGE | Services launched | Use case |
|---|---|---|
| `dev` | postgres + web + nginx | Local development |
| `prod` | web + nginx (no postgres) | Production (postgres external, e.g. RDS) |

### Switching environments

```bash
# Edit .env
sed -i 's/^STAGE=.*/STAGE=prod/' .env   # Linux
# or:
sed -i '' 's/^STAGE=.*/STAGE=prod/' .env   # macOS

# Re-create the stack
make restart

# Confirm
make validate-config
```

### Important when STAGE=prod

Switching to `prod` also requires pointing `DATABASE_HOST` at your external server:

```bash
DATABASE_HOST=tudb.cluster-xxx.us-east-1.rds.amazonaws.com
DATABASE_PORT=5432
```

The local `postgres` service is intentionally skipped in this mode — that's a feature, not a bug.

### Verifying

```bash
make validate-config   # prints the detected STAGE
docker compose ps      # lists the containers actually running
```

### Under the hood

The Makefile reads `STAGE` from `.env` and exports `COMPOSE_PROFILES`:

- `STAGE=dev` → `COMPOSE_PROFILES=dev` → services with `profiles: [dev]` activate (postgres).
- `STAGE=prod` → `COMPOSE_PROFILES=` (empty) → only services without any profile run (web, nginx).

This uses [Docker Compose profiles](https://docs.docker.com/compose/profiles/) natively — no override files, no hacks. `required: false` on the `web → postgres` dependency lets the web service start without the local DB when `prod` is active.

## Django admin (custom URL + honeypot)

The Django admin is **not** at `/admin/`. The literal `/admin/` is a decoy served by [django-honeypot](https://pypi.org/project/django-honeypot/) — bots that scan that path get a fake login form. The real admin lives at whatever you set in `.env`:

```bash
INTERNAL_ADMIN_URL=admin-Hk9_x2lT/
```

### Generate a value

```bash
python -c "import secrets; print(f'admin-{secrets.token_urlsafe(6)}/')"
```

Paste the output into `.env`, then `make restart` so the container re-reads the file.

### Fail-fast

`INTERNAL_ADMIN_URL` is **required**. If it's missing, empty, or set to the literal `admin/`, `settings.py` raises `ImproperlyConfigured` and the app refuses to boot. You'll see the message before the server ever binds a port. Verify without bringing the stack up:

```bash
make validate-config
```

### What this pattern is and isn't

It's defense in depth — cheap, drops 99% of automated scan traffic against the admin. It is **not** a substitute for:

- Strong passwords on admin accounts (use a generator, ≥16 chars)
- 2FA on admin accounts (`django-otp` or similar)
- HTTPS in production (never expose admin over HTTP)
- IP allow-listing or VPN if the admin is internal-only
- Rate limiting (`django-ratelimit` or nginx `limit_req`)

Treat `INTERNAL_ADMIN_URL` as operational secret: don't commit `.env`, don't log it, rotate if leaked.

## Security (Hosts, CSRF, CORS)

Three separate Django lists, all driven from `.env`. They look similar but their formats and purposes differ — mixing them up is the most common source of CSRF/CORS bugs.

| Variable | Format | Purpose |
|---|---|---|
| `ALLOWED_HOSTS` | `host1,host2` (no scheme, no port) | Reject requests whose `Host` header isn't in the list |
| `CSRF_TRUSTED_ORIGINS` | `http://host:port,https://host` | Accept cross-origin POSTs from these origins (admin login via nginx counts) |
| `CORS_ALLOWED_ORIGINS` | `http://host:port,https://host` | Let the browser fetch from these origins (separate frontend → backend) |

### When to edit each

- **`ALLOWED_HOSTS`** — adding a new domain where the backend is reachable.
  ```
  ALLOWED_HOSTS=localhost,127.0.0.1,thiup.com,*.thiup.com
  ```
- **`CSRF_TRUSTED_ORIGINS`** — accessing the admin from a new host or port. Django 4+ requires an *exact* match including scheme and port.
  ```
  CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://localhost:3000,https://thiup.com
  ```
- **`CORS_ALLOWED_ORIGINS`** — the frontend (e.g. Next.js on `:3000`) makes `fetch`/`axios` calls to the backend.
  ```
  CORS_ALLOWED_ORIGINS=http://localhost:3000
  ```

### Applying changes

Containers read `.env` once at start. `docker compose restart` does **not** reload it:

```bash
make restart
```

## Static files

Where static assets (Django admin CSS/JS, DRF, drf-yasg) come from depends on
`USE_AWS_STORAGE_SYSTEM`:

- **`false`** (local) — nginx serves `/static/*` from `static_volume`
  (`STATIC_ROOT=/app/staticfiles`). With `DEBUG=True`, `runserver` also serves them
  directly, so no extra step is needed for the admin to render.
- **`true`** (object storage) — `collectstatic` uploads them to the bucket
  (S3 / Cloudflare R2) and they're served from `AWS_S3_CUSTOM_DOMAIN`. In AWS the
  CI pipeline runs `collectstatic` on every deploy; locally, run it on demand when
  assets change:
  ```bash
  docker compose run --rm web python manage.py collectstatic --noinput
  ```

> The auto `collectstatic` compose service was removed — on object storage it
> re-uploaded every asset to the remote bucket on each `make up`, slowing startup.
> Use `--clear` is intentionally avoided: S3/R2 backends reject the empty-key
> existence check it triggers.

## Media URLs (always absolute)

File fields (`FileField` on `ThreadFile`) always expose **absolute** URLs in API responses, admin templates, and anywhere else `.url` is accessed:

```json
{ "file": "https://cdn.thiup.com/uploads/7bd49b04fdac4f16a239794674ad47a4.png" }
```

This makes a separate frontend (`http://localhost:3000`) just work — no per-request URL rewriting on the client side.

**Uploads get a random name.** `ThreadFile.file` uses an `upload_to` callable that
stores each file as `uploads/<uuid>.<ext>` — the original filename never leaks
(privacy) and collisions are impossible.

### How it's wired

Two backends, one shape:

| `USE_AWS_STORAGE_SYSTEM` | Backend | URLs come from |
|---|---|---|
| `true` | `storages.backends.s3boto3.S3Boto3Storage` (AWS S3 or **Cloudflare R2**) | The bucket (already absolute) |
| `false` | `app.storages.AbsoluteUrlFileSystemStorage` | `MEDIA_BASE_URL` + the relative path |

`AbsoluteUrlFileSystemStorage` is a `FileSystemStorage` subclass that overrides `url()` to prepend `MEDIA_BASE_URL`. Everything else (saving, deleting, listing) is inherited. The provider (AWS S3 vs R2) is chosen by config — set `AWS_S3_ENDPOINT_URL` to the R2 endpoint, `AWS_S3_REGION_NAME=auto`, and the R2 keys.

> **Tests never touch the bucket.** Under pytest, `settings.py` forces an in-memory
> storage backend, so a connected R2/S3 bucket is never written to during tests.

### Configure

```bash
# dev (default)
MEDIA_BASE_URL=http://localhost:8000

# prod without S3
MEDIA_BASE_URL=https://api.tu-dominio.com
```

No trailing slash. Ignored when `USE_AWS_STORAGE_SYSTEM=true`.

### When you add new code

- **DRF ViewSets** (`get_serializer`-based): nothing to do — the auto-built `ImageField`/`FileField` on the serializer will use the storage backend and get an absolute URL.
- **Custom `to_representation`** that touches `instance.file.url` directly: nothing to do — `.url` already returns absolute thanks to the storage override.
- **Manually-constructed serializers** (`MySerializer(data, many=True)`): still pass `context={'request': request}` for any non-file fields that depend on the request (HyperlinkedIdentityField, view-name reverses, etc.). For file URLs specifically, you no longer need it.

## Seed data

The reaction catalog (`Reaction` rows) is loaded from a JSON fixture
(`app/fixtures/reactions.json`):

```bash
make load_fixtures
```

Idempotent — rows that already exist are skipped. Reactions are emoji-only now
(the old image-based reactions and the `Miniature`/avatar feature were removed,
along with the bundled `media/` binaries).

To fill the DB with throwaway content for local testing:

```bash
make add_dummy_threads 50
```

| Concept | Where it lives | In git? | Loaded with |
|---|---|---|---|
| Reaction catalog | `app/fixtures/reactions.json` | yes | `make load_fixtures` |
| Dummy threads | generated | no | `make add_dummy_threads [N]` |
| User uploads | the bucket (R2/S3) or local `media/` | no | runtime, by users |

## Migrations

The project uses a hybrid pattern: manual Make targets for intentional control, plus an ephemeral `migration` service that runs automatically in dev.

### Commands

| Command | Purpose |
|---|---|
| `make makemigrations` | Generate migrations from model changes |
| `make makemigrations-check` | Fail if models have unmade migrations (no write — for CI) |
| `make sqlmigrate APP=x MIGRATION=N` | Print the SQL a migration will execute |

Applying migrations is **not** a manual target: in dev the `migration` compose
service runs `migrate` automatically on `make up`; in AWS the CI pipeline runs it
on every deploy (with optional rollback-to-common-ancestor — see
[`ci/infra/README.md`](ci/infra/README.md)).

### Automatic in dev

With `STAGE=dev`, `make up` chains startup like this:

```
postgres (healthy)
    │
    ▼
migration  (runs `manage.py migrate --noinput`, exits 0)
    │
    ▼
web        (only starts after the migration step succeeds)
    │
    ▼
nginx
```

Outcome: every dev startup arrives at a schema that matches the model code. If a migration fails, `web` doesn't start (fail-fast).

### NOT in production

With `STAGE=prod`, the `migration` service is skipped (it carries `profiles: [dev]`), and `web`'s dependency on it is gated by `required: false`. This is deliberate:

- Multi-instance deploys would race two `migrate` runs against the same DB.
- A long migration would block the rollout.
- A failed migration would take down the whole deploy with no clean rollback path.

In AWS the CI pipeline applies migrations in a dedicated ephemeral ECS task
**before** rolling out the new image (an optional gated step can also roll back
migrations the deployed branch doesn't have). See
[`ci/infra/README.md`](ci/infra/README.md).

### Typical workflow

```bash
vim app/models/thread.py                      # change a model
make makemigrations                           # generates app/migrations/00XX_*.py
make sqlmigrate APP=app MIGRATION=00XX        # optional: review the SQL
make restart                                  # the migration service applies it
git add app/migrations/00XX_*.py
git commit -m "Migration for <change>"
```

### Tips

- **Commit your migrations.** They are code; they belong in the repo.
- **After `git pull`**, run `make restart` to apply any migrations a teammate added.
- **Don't edit applied migrations.** Generate a new one on top instead.

## Dependency management

The project uses the standard [pip-tools](https://pip-tools.readthedocs.io/) three-file pattern:

| File | Purpose | Edit by hand? |
|---|---|---|
| `requirements.in` | Top-level production deps (flexible ranges) | Yes — source of truth |
| `requirements.dev` | Top-level dev deps (inherits from `.in`) | Yes |
| `requirements.txt` | Generated lockfile (exact versions + transitives) | No — regenerate with `pip-compile` |

### Add a dependency

1. Edit `requirements.in` (production) or `requirements.dev` (development).
2. Use a range, e.g. `Django>=4.2,<5.0`.
3. Regenerate the lockfile: `pip-compile requirements.in` (run inside the container, e.g. `docker compose run --rm web pip-compile requirements.in`).
4. Rebuild the image so it picks up the change: `make dependencies`
5. Verify: `make test`

### Why this pattern

- **Reproducibility**: production runs the exact versions that were tested.
- **Lean prod image**: the prod image (`ci/aws.Dockerfile`) installs `requirements.txt` only. The **local** image also installs `requirements.dev` (pytest, etc.) so `make test` runs without extra setup.
- **Auditability**: every version change shows up in the `requirements.txt` diff.
- **Controlled upgrades**: the range in `.in` defines what is acceptable; the lockfile pins what is active.

> `make dependencies` rebuilds the local `web` image (to pick up requirements
> changes); `make test` then runs against that image.

## Background jobs (momentum) — no Celery

For You ranking is precomputed by a management command — not a per-request job and
not a Celery worker (Celery/RabbitMQ were removed). `recompute_momentum` runs every
10 minutes:

- **Local:** the `momentum` compose service (a `while true; recompute_momentum; sleep 600` loop).
- **AWS:** EventBridge Scheduler triggers an ephemeral Fargate task.

Run it on demand with `make recompute_momentum`.

## Deployment (AWS)

The backend deploys to AWS ECS/Fargate from a single CloudFormation stack
(`ci/infra/ecs.yml`): a public ALB → Fargate service, an S3-compatible asset bucket
(AWS S3 or **Cloudflare R2**), the EventBridge momentum scheduler, and a CodeBuild
CI/CD pipeline (build → collectstatic → migrate → deploy, with immutable per-commit
image tags and optional migration rollback).

Full deploy guide, parameters, and the manual GitHub-connection steps:
**[`ci/infra/README.md`](ci/infra/README.md)**.
