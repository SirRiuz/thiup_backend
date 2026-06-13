FROM python:3.12-slim-bullseye

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

# Shell form so SERVER_PORT (if ECS passes one) expands; defaults to 8000.
CMD gunicorn \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --error-logfile=- \
    --access-logfile=- \
    --bind=0.0.0.0:${SERVER_PORT:-8000} \
    --workers=3 \
    core.wsgi
