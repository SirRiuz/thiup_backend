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
make down
make up-d

# Confirm
make stage
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
make stage   # prints the detected STAGE and the services that should run
make ps      # lists the containers actually running
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

Paste the output into `.env`, then `make down && make up-d` so the container re-reads the file.

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
make down
make up-d
```

## Static files

Static assets (Django admin CSS/JS, DRF, drf-yasg, anything in app `static/` dirs) are served by **nginx**, not Django. That's the production-grade pattern and the project keeps dev consistent with it.

### How it works

1. `STATIC_URL = "/static/"`, `STATIC_ROOT = "/app/staticfiles/"` in `core/settings.py`.
2. With `STAGE=dev`, an ephemeral `collectstatic` service runs `python manage.py collectstatic --noinput --clear` before `web` starts.
3. The output goes into the `static_volume`, which nginx mounts read-only.
4. Browser requests `/static/admin/css/base.css` → nginx serves it directly from `/app/staticfiles/admin/css/base.css`.

### Automatic dev flow

```
make up-d
    │
    ▼
postgres (healthy)
    │
    ▼
migration   collectstatic   (run in parallel after postgres is healthy)
    │           │
    └─────┬─────┘
          ▼
        web   (starts only after both succeed)
          │
          ▼
        nginx (serves /static/* from the shared volume)
```

### Regenerating manually

After installing a library that ships its own assets (`drf-yasg`, `debug-toolbar`, ...) or editing assets in `apps/*/static/`:

```bash
make collectstatic
```

No restart needed — the file lands in the shared volume and nginx serves it on the next request.

### In production

`collectstatic` is dev-only here (`profiles: ["dev"]`). For prod, either:
- Add a `RUN python manage.py collectstatic --noinput` step to the `Dockerfile` so the image ships with assets baked in (faster boot, no race on multi-instance deploys), or
- Set `USE_AWS_STORAGE_SYSTEM=true` so `collectstatic` uploads to S3 and nginx is bypassed for static traffic entirely.

## Media URLs (always absolute)

File fields (`ImageField`, `FileField`) always expose **absolute** URLs in API responses, admin templates, and anywhere else `.url` is accessed:

```json
{ "miniature": "http://localhost:8000/media/miniature/Frame-1.png" }
```

This makes a separate frontend (`http://localhost:3000`) just work — no per-request URL rewriting on the client side.

### How it's wired

Two backends, one shape:

| `USE_AWS_STORAGE_SYSTEM` | Backend | URLs come from |
|---|---|---|
| `true` | `storages.backends.s3boto3.S3Boto3Storage` | The bucket (already absolute) |
| `false` | `apps.default.storages.AbsoluteUrlFileSystemStorage` | `MEDIA_BASE_URL` + the relative path |

`AbsoluteUrlFileSystemStorage` is a `FileSystemStorage` subclass that overrides `url()` to prepend `MEDIA_BASE_URL`. Everything else (saving, deleting, listing) is inherited.

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

The project ships binaries in `media/reactions/` and `media/masks/` that are meant to land in the DB as `Reaction` and `Miniature` rows. Ingestion is a Django **management command**, not a fixture:

```bash
make seed
```

Idempotent — running it twice is a no-op (it filters out Django's collision-renamed copies from prior runs).

### Why a command, not a fixture

Django fixtures store `ImageField` values as plain path strings — they don't move or transform files. The seed data here is "scan a directory of binaries and create one row per file, letting Django's storage handle the file write." A management command expresses that directly; a fixture would require pre-renaming each binary to match the path string baked into the JSON, doubling the files in the repo for no behavior change.

If you later add a static catalog (e.g. `Tag` rows, default permissions), **that** is the natural place for a JSON fixture under `apps/<app>/fixtures/`. The two patterns coexist; you pick the one that fits the data.

### Quick reference

| Concept | Where it lives | In git? | Loaded with |
|---|---|---|---|
| Seed binaries | `media/reactions/`, `media/masks/` | yes | `make seed` |
| Catalog rows (when added) | `apps/<app>/fixtures/*.json` | yes | `python manage.py loaddata <name>` |
| User uploads | `media/` (subdirs Django chooses) | no (gitignored) | runtime, by users |

`media/` and `fixtures/` are different things — `media/` holds binary content (some of it bundled seed assets, the rest user-uploaded), `fixtures/` would hold versioned catalog JSON. Don't mix them.

## Migrations

The project uses a hybrid pattern: manual Make targets for intentional control, plus an ephemeral `migration` service that runs automatically in dev.

### Commands

| Command | Purpose |
|---|---|
| `make migrate` | Apply pending migrations |
| `make makemigrations` | Generate migrations from model changes |
| `make showmigrations` | Show applied vs pending state |
| `make sqlmigrate APP=x MIGRATION=N` | Print the SQL a migration will execute |
| `make migrate-rollback APP=x MIGRATION=N` | Revert to a specific migration (leaves N applied) |
| `make migrate-fake APP=x MIGRATION=N` | Mark a migration as applied **without running it** |

All targets use `docker compose run --rm web …` so they work even when the stack is down.

### Automatic in dev

With `STAGE=dev`, `make up-d` chains startup like this:

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

The production playbook is to apply migrations in a dedicated CI/CD step **before** rolling out the new image — typically by running `make migrate` against the target environment, or a similar one-shot job.

### Typical workflow

```bash
vim apps/threads/models/thread.py     # change a model
make makemigrations                   # generates apps/threads/migrations/00XX_*.py
make sqlmigrate APP=threads MIGRATION=00XX   # optional: review the SQL
make migrate                          # apply
make showmigrations                   # confirm
git add apps/threads/migrations/00XX_*.py
git commit -m "Migration for <change>"
```

### Tips

- **Commit your migrations.** They are code; they belong in the repo.
- **After `git pull`**, run `make migrate` to apply any migrations a teammate added.
- **Don't edit applied migrations.** Generate a new one on top instead.

## Dependency management

The project uses the standard [pip-tools](https://pip-tools.readthedocs.io/) three-file pattern:

| File | Purpose | Edit by hand? |
|---|---|---|
| `requirements.in` | Top-level production deps (flexible ranges) | Yes — source of truth |
| `requirements.dev` | Top-level dev deps (inherits from `.in`) | Yes |
| `requirements.txt` | Generated lockfile (exact versions + transitives) | No — regenerated via `make dependencies` |

### Add a dependency

1. Edit `requirements.in` (production) or `requirements.dev` (development).
2. Use a range, e.g. `Django>=4.2,<5.0`.
3. Regenerate the lockfile: `make dependencies`
4. Rebuild the image: `make build`
5. Verify: `make test`

### Upgrade dependencies

```bash
make dependencies-upgrade   # bumps to latest within the .in ranges
make build
make test                   # surface breaking changes
```

### Why this pattern

- **Reproducibility**: production runs the exact versions that were tested.
- **Lean prod image**: the Docker image installs `requirements.txt` only — no `pytest`, `factory-boy`, etc.
- **Auditability**: every version change shows up in the `requirements.txt` diff.
- **Controlled upgrades**: the range in `.in` defines what is acceptable; the lockfile pins what is active.
