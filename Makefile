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

.PHONY: help build up down restart logs logs-web logs-db logs-nginx \
        logs-momentum \
        shell shell-db makemigrations makemigrations-check sqlmigrate \
        createsuperuser \
        load_fixtures add_dummy_threads recompute_momentum \
        validate-config \
        test clean clean-volumes \
        dependencies

help:
	@echo ''
	@echo 'Available commands:'
	@echo ''
	@echo '  Lifecycle:'
	@echo '    make build           Build Docker images'
	@echo '    make up              Bring up stack in foreground (Ctrl+C to stop)'
	@echo '    make down            Stop and remove containers'
	@echo '    make restart         Full restart'
	@echo ''
	@echo '  Logs / status:'
	@echo '    make logs            Tail logs for all services'
	@echo '    make logs-web        Tail Django logs'
	@echo '    make logs-db         Tail PostgreSQL logs'
	@echo '    make logs-nginx      Tail nginx logs'
	@echo '    make logs-momentum   Tail the momentum recompute loop logs'
	@echo ''
	@echo '  Django:'
	@echo '    make shell             Django shell'
	@echo '    make shell-db          psql against the DB'
	@echo '    make createsuperuser   Create a superuser'
	@echo '    make load_fixtures     Load predefined fixtures (reactions, …)'
	@echo '    make add_dummy_threads [N]  Create N dummy threads (default 10). E.g. make add_dummy_threads 50'
	@echo '    make recompute_momentum  Recompute For You momentum NOW (the scheduler already runs it every 10 min)'
	@echo '    make validate-config   Load settings.py once and surface config errors'
	@echo ''
	@echo '  Migrations:'
	@echo '    make makemigrations        Generate migrations from model changes'
	@echo '    make makemigrations-check  Fail if models have unmade migrations (no write)'
	@echo '    make sqlmigrate            Print SQL for a migration (APP=x MIGRATION=N)'
	@echo ''
	@echo '  Testing:'
	@echo '    make test            pytest with coverage (gate >=70%); no running stack needed'
	@echo ''
	@echo '  Dependencies:'
	@echo '    make dependencies    Rebuild the web image to pick up requirements changes'
	@echo ''
	@echo '  Cleanup:'
	@echo '    make clean           Remove containers/networks (keep volumes)'
	@echo '    make clean-volumes   DESTROYS data — removes volumes too'
	@echo ''

# ─── Lifecycle ──────────────────────────────────────────────

build:
	docker compose build

up:
	docker compose up

down:
	docker compose down

restart:
	docker compose down
	docker compose up -d

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

# ─── Django admin ───────────────────────────────────────────

shell:
	docker compose exec web python manage.py shell

shell-db:
	docker compose exec postgres psql -U $(shell grep ^DATABASE_USER .env | cut -d= -f2) -d $(shell grep ^DATABASE_NAME .env | cut -d= -f2) -p $(shell grep ^DATABASE_PORT .env | cut -d= -f2)

makemigrations:
	docker compose run --rm web python manage.py makemigrations

makemigrations-check:
	docker compose run --rm web python manage.py makemigrations --check --dry-run

sqlmigrate:
	@if [ -z "$(APP)" ] || [ -z "$(MIGRATION)" ]; then \
		echo 'Usage: make sqlmigrate APP=<app_name> MIGRATION=<number>'; \
		echo 'Example: make sqlmigrate APP=threads MIGRATION=0005'; \
		exit 1; \
	fi
	docker compose run --rm web python manage.py sqlmigrate $(APP) $(MIGRATION)

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
	docker compose run --rm web pytest --cov --cov-report=term-missing --cov-report=html:htmlcov --cov-fail-under=70

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

# ─── Dependencies ───────────────────────────────────────────

dependencies:
	docker compose build web
	@echo 'Image rebuilt — picks up requirements.txt / requirements.dev changes.'
