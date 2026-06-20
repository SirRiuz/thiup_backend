# ECR Public mirror of the official image — avoids Docker Hub's 429 pull limit.
# bookworm (Debian 12) ships libpq 15 with SNI support, required to connect to
# Neon (bullseye's libpq 13 lacks SNI -> "Endpoint ID is not specified").
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=core.settings

# System dependencies — same set the dev Dockerfile installs (pango/xvfb/libpq
# are load-bearing: WeasyPrint/curl/psycopg2 build against them).
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  build-essential \
  libcurl4-openssl-dev \
  libffi-dev \
  libpq-dev \
  pango1.0-tools \
  python3-dev \
  wget \
  xvfb \
  xauth \
  && rm -rf /var/lib/apt/lists/* \
  && apt-get purge --auto-remove \
  && apt-get clean

WORKDIR /code

COPY requirements.txt .

RUN pip install --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt

COPY . .

# GIT_SHA at the end — declaring it earlier would bust the cache for every
# layer below it on each commit.
ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA

EXPOSE 8000

# Shell form so SERVER_PORT / GUNICORN_* (if ECS passes them) expand.
# Tuning for I/O-bound load (Postgres + encryption) on small instances:
#  - gthread worker: few PROCESSES (RAM ~= one Django copy each) x several
#    cheap THREADS (shared memory). 2 workers x 4 threads = 8 concurrent.
#  - --preload: load the app once in the master then fork -> copy-on-write
#    sharing roughly halves real memory (safe: Django connects lazily).
#  - max-requests + jitter: recycle workers to contain memory leaks.
#  - timeouts/keep-alive tuned; warning-level logs to reduce noise.
CMD gunicorn \
    --worker-class gthread \
    --workers ${GUNICORN_WORKERS:-2} \
    --threads ${GUNICORN_THREADS:-4} \
    --preload \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --timeout 60 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --log-level warning \
    --access-logfile=- \
    --error-logfile=- \
    --bind=0.0.0.0:${SERVER_PORT:-8000} \
    core.wsgi
