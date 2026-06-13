# ============================================================
# Makefile — thiup_backend
# ============================================================

# ─── Stage detection ────────────────────────────────────────
# Reads STAGE from .env and translates it to COMPOSE_PROFILES.
#   dev  → COMPOSE_PROFILES=dev   (postgres + web + nginx)
#   prod → COMPOSE_PROFILES=      (web + nginx only)
# Every docker compose call below inherits this exported value.
# ────────────────────────────────────────────────────────────
STAGE := $(shell grep -E '^STAGE=' .env 2>/dev/null | cut -d= -f2)
ifeq ($(STAGE),dev)
    COMPOSE_PROFILES := dev
else
    COMPOSE_PROFILES :=
endif
export COMPOSE_PROFILES

.PHONY: help stage build up up-d down restart logs logs-web logs-db logs-nginx \
        logs-momentum \
        ps shell shell-db migrate makemigrations showmigrations sqlmigrate \
        migrate-rollback migrate-fake collectstatic createsuperuser \
        load_fixtures add_dummy_threads recompute_momentum \
        validate-config \
        test test-fast test-coverage clean clean-volumes \
        dependencies dependencies-upgrade dependencies-dev dependencies-sync

help:
	@echo ''
	@echo 'Available commands:'
	@echo ''
	@echo '  Stage:'
	@echo '    make stage           Show current STAGE and which services will run'
	@echo ''
	@echo '  Lifecycle:'
	@echo '    make build           Build Docker images'
	@echo '    make up              Bring up stack in foreground (Ctrl+C to stop)'
	@echo '    make up-d            Bring up stack detached (background)'
	@echo '    make down            Stop and remove containers'
	@echo '    make restart         Full restart'
	@echo ''
	@echo '  Logs / status:'
	@echo '    make logs            Tail logs for all services'
	@echo '    make logs-web        Tail Django logs'
	@echo '    make logs-db         Tail PostgreSQL logs'
	@echo '    make logs-nginx      Tail nginx logs'
	@echo '    make logs-momentum   Tail the momentum recompute loop logs'
	@echo '    make ps              Container status'
	@echo ''
	@echo '  Django:'
	@echo '    make shell             Django shell'
	@echo '    make shell-db          psql against the DB'
	@echo '    make collectstatic     Collect static files'
	@echo '    make createsuperuser   Create a superuser'
	@echo '    make load_fixtures     Load predefined fixtures (reactions, …)'
	@echo '    make add_dummy_threads [N]  Create N dummy threads (default 10). E.g. make add_dummy_threads 50'
	@echo '    make recompute_momentum  Recompute For You momentum NOW (the scheduler already runs it every 10 min)'
	@echo '    make validate-config   Load settings.py once and surface config errors'
	@echo ''
	@echo '  Migrations:'
	@echo '    make migrate           Apply pending migrations'
	@echo '    make makemigrations    Generate migrations from model changes'
	@echo '    make showmigrations    Inspect applied / pending migrations'
	@echo '    make sqlmigrate        Print SQL for a migration (APP=x MIGRATION=N)'
	@echo '    make migrate-rollback  Revert to a migration (APP=x MIGRATION=N)'
	@echo '    make migrate-fake      Mark migration applied without running (APP=x MIGRATION=N)'
	@echo ''
	@echo '  Testing:'
	@echo '    make test            pytest with coverage (terminal + HTML)'
	@echo '    make test-fast       pytest -x (stop on first failure, no coverage)'
	@echo '    make test-coverage   pytest with coverage gate (>=70%) for CI'
	@echo ''
	@echo '  Dependencies:'
	@echo '    make dependencies         Compile requirements.in -> requirements.txt'
	@echo '    make dependencies-upgrade Upgrade all deps to latest (within .in ranges)'
	@echo '    make dependencies-dev     Install dev deps inside the running web container'
	@echo '    make dependencies-sync    pip-sync container env to requirements.txt'
	@echo ''
	@echo '  Cleanup:'
	@echo '    make clean           Remove containers/networks (keep volumes)'
	@echo '    make clean-volumes   DESTROYS data — removes volumes too'
	@echo ''

# ─── Stage ──────────────────────────────────────────────────

stage:
	@echo ''
	@echo 'STAGE: $(STAGE)'
	@if [ "$(STAGE)" = "dev" ]; then \
		echo 'Active services: postgres + web + nginx'; \
	elif [ "$(STAGE)" = "prod" ]; then \
		echo 'Active services: web + nginx (postgres is external)'; \
	else \
		echo 'WARNING: STAGE is unset or not dev/prod in .env.'; \
		echo '  Edit .env and set STAGE=dev or STAGE=prod.'; \
	fi
	@echo ''

# ─── Lifecycle ──────────────────────────────────────────────

build:
	docker compose build

up:
	docker compose up

up-d: stage
	docker compose up -d
	@echo ''
	@echo 'Stack is up. Tail logs with: make logs'
	@echo 'Backend reachable at: http://localhost:$(shell grep ^SERVER_PORT .env | cut -d= -f2)'

down:
	docker compose down

restart: down up-d

# ─── Logs / status ──────────────────────────────────────────

logs:
	docker compose logs -f

logs-web:
	docker compose logs -f web

logs-db:
	docker compose logs -f postgres

logs-nginx:
	docker compose logs -f nginx

logs-momentum:
	docker compose logs -f momentum

ps:
	docker compose ps

# ─── Django admin ───────────────────────────────────────────

shell:
	docker compose exec web python manage.py shell

shell-db:
	docker compose exec postgres psql -U $(shell grep ^DATABASE_USER .env | cut -d= -f2) -d $(shell grep ^DATABASE_NAME .env | cut -d= -f2) -p $(shell grep ^DATABASE_PORT .env | cut -d= -f2)

migrate:
	docker compose run --rm web python manage.py migrate

makemigrations:
	docker compose run --rm web python manage.py makemigrations

showmigrations:
	docker compose run --rm web python manage.py showmigrations

sqlmigrate:
	@if [ -z "$(APP)" ] || [ -z "$(MIGRATION)" ]; then \
		echo 'Usage: make sqlmigrate APP=<app_name> MIGRATION=<number>'; \
		echo 'Example: make sqlmigrate APP=threads MIGRATION=0005'; \
		exit 1; \
	fi
	docker compose run --rm web python manage.py sqlmigrate $(APP) $(MIGRATION)

migrate-rollback:
	@if [ -z "$(APP)" ] || [ -z "$(MIGRATION)" ]; then \
		echo 'Usage: make migrate-rollback APP=<app_name> MIGRATION=<number>'; \
		echo 'Example: make migrate-rollback APP=threads MIGRATION=0004'; \
		echo '         (reverts to leave 0004 applied; undoes 0005, 0006, ...)'; \
		exit 1; \
	fi
	docker compose run --rm web python manage.py migrate $(APP) $(MIGRATION)

migrate-fake:
	@if [ -z "$(APP)" ] || [ -z "$(MIGRATION)" ]; then \
		echo 'Usage: make migrate-fake APP=<app_name> MIGRATION=<number>'; \
		echo 'WARNING: marks migration as applied WITHOUT running it.'; \
		exit 1; \
	fi
	docker compose run --rm web python manage.py migrate $(APP) $(MIGRATION) --fake

collectstatic:
	docker compose run --rm web python manage.py collectstatic --noinput --clear
	@echo ''
	@echo 'Static files written to /app/staticfiles/. nginx will serve them at /static/*.'

createsuperuser:
	docker compose exec web python manage.py createsuperuser

load_fixtures:
	docker compose run --rm web python manage.py load_fixtures

# Usage:
#   make add_dummy_threads      # default 10 threads
#   make add_dummy_threads 50   # 50 top-level threads
#
# The positional number is parsed from MAKECMDGOALS, and we register an
# empty rule for it so `make` doesn't complain "No rule to make target".
ifneq (,$(filter add_dummy_threads,$(MAKECMDGOALS)))
ADT_NUM := $(word 2,$(MAKECMDGOALS))
ifneq (,$(ADT_NUM))
$(eval $(ADT_NUM):;@:)
endif
endif

add_dummy_threads:
	docker compose run --rm web python manage.py add_dummy_threads --number $(or $(ADT_NUM),10)

# For You momentum recompute. An external scheduler runs it every 10 min
# (the `momentum` compose service locally, EventBridge in prod); this target
# is for manual runs.
recompute_momentum:
	docker compose exec -T web python manage.py recompute_momentum

validate-config:
	docker compose run --rm web python manage.py check
	@docker compose run --rm web python manage.py shell -c "from django.conf import settings; print(f'STAGE              = {settings.STAGE}'); print(f'INTERNAL_ADMIN_URL = {settings.INTERNAL_ADMIN_URL}')"

# ─── Testing ────────────────────────────────────────────────

test:
	docker compose exec web pytest --cov --cov-report=term-missing --cov-report=html:htmlcov

test-fast:
	docker compose exec web pytest -x --tb=short

test-coverage:
	docker compose exec web pytest --cov --cov-report=term --cov-report=xml --cov-fail-under=70

# ─── Cleanup ────────────────────────────────────────────────

clean:
	docker compose down --remove-orphans

clean-volumes:
	@echo 'This deletes PostgreSQL data AND static/media volumes.'
	@printf 'Continue? [y/N] '; \
	read confirm; \
	if [ "$$confirm" = "y" ]; then \
		docker compose down -v --remove-orphans; \
	else \
		echo 'Cancelled'; \
	fi

# ─── Dependencies (pip-tools workflow) ──────────────────────

dependencies:
	docker compose run --rm web sh -c "pip install pip-tools && pip-compile --no-emit-index-url --output-file=requirements.txt requirements.in"
	@echo ''
	@echo 'requirements.txt regenerated from requirements.in.'
	@echo 'Review the diff: git diff requirements.txt'

dependencies-upgrade:
	docker compose run --rm web sh -c "pip install pip-tools && pip-compile --no-emit-index-url --upgrade --output-file=requirements.txt requirements.in"
	@echo ''
	@echo 'requirements.txt upgraded to latest versions within the .in ranges.'
	@echo 'Run the test suite to surface breaking changes: make test'

dependencies-dev:
	docker compose exec web pip install -r requirements.dev
	@echo 'Dev dependencies installed in the running web container.'

dependencies-sync:
	docker compose run --rm web sh -c "pip install pip-tools && pip-sync requirements.txt"
	@echo 'Environment synced with requirements.txt (stale deps removed).'
