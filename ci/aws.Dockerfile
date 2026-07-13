# ECR Public mirror of the official image — avoids Docker Hub's 429 pull limit.
# bookworm (Debian 12) ships libpq 15 with SNI support, required to connect to
# Neon (bullseye's libpq 13 lacks SNI -> "Endpoint ID is not specified").
#
# Multi-stage: the builder compiles psycopg2 (the only sdist in requirements —
# everything else ships manylinux wheels), the runtime keeps just libpq5. This
# drops build-essential/python3-dev (~330 MB) plus pango/xvfb/xauth/wget, which
# nothing in requirements.txt uses. A smaller image pulls faster, and Fargate
# bills from the START of the image pull — the ephemeral momentum task pays
# that pull on every scheduled run.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm AS builder

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  build-essential \
  libpq-dev \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Self-contained venv so the runtime stage copies ONE directory.
RUN python -m venv /opt/venv \
  && /opt/venv/bin/pip install --upgrade pip \
  && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# botocore ships the API definitions of EVERY AWS service (tens of MB);
# this app only talks to S3-compatible storage (R2). Keep the s3 service
# dir; the loose files at data/ root (endpoints/partitions/retry configs)
# are shared plumbing and stay untouched (-type d only).
RUN cd /opt/venv/lib/python3.12/site-packages/botocore/data \
  && find . -maxdepth 1 -mindepth 1 -type d ! -name "s3" -exec rm -rf {} +


FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=core.settings \
    PATH="/opt/venv/bin:$PATH"

# libpq5: runtime shared library for the compiled psycopg2.
RUN apt-get update \
  && apt-get install -y --no-install-recommends libpq5 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /code

COPY . .

# GIT_SHA at the end — declaring it earlier would bust the cache for every
# layer below it on each commit.
ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA

# Drop root: the web process only READS /code and /opt/venv (media goes to R2,
# static to S3, logs to stdout — nothing is written to the image at runtime),
# so a container escape/RCE lands as an unprivileged user. Port 8000 is >1024,
# so no privileged bind is needed.
RUN useradd --system --uid 10001 --create-home appuser
USER appuser

EXPOSE 8000

# Shell form so SERVER_PORT / GUNICORN_* (if ECS passes them) expand.
# Tuning for I/O-bound load (Postgres + encryption) on a small Fargate task
# (0.25 vCPU / 512 MB):
#  - gthread worker: PROCESSES cost RAM (~= one Django copy each), THREADS are
#    cheap (shared memory). 1 worker x 4 threads = 4 concurrent requests —
#    plenty at current traffic; scale with GUNICORN_WORKERS when needed.
#  - --preload: load the app once in the master then fork (safe: Django
#    connects lazily); keeps worker recycling cheap.
#  - max-requests + jitter: recycle workers to contain memory leaks.
#  - timeouts/keep-alive tuned; warning-level logs to reduce noise.
#  - NO access log: it logged EVERY request to stdout regardless of
#    --log-level (the access log is independent of it) — CloudWatch
#    ingestion cost and against the sparse-logging constraint. Errors
#    still go to stderr.
CMD gunicorn \
    --worker-class gthread \
    --workers ${GUNICORN_WORKERS:-1} \
    --threads ${GUNICORN_THREADS:-4} \
    --preload \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --timeout 60 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --log-level warning \
    --error-logfile=- \
    --bind=0.0.0.0:${SERVER_PORT:-8000} \
    core.wsgi
