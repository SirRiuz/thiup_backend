FROM python:3.12

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install env dependencies in one single command/layer
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

WORKDIR /app

# Local image installs prod + dev deps (pytest, etc.) so tests run without
# extra setup. The prod image (ci/aws.Dockerfile) installs requirements.txt only.
COPY requirements.txt requirements.in requirements.dev /app/
RUN pip install --upgrade pip \
    && pip install -r requirements.txt -r requirements.dev

# No EXPOSE: the port comes from .env via SERVER_PORT.
# No CMD: defined in docker-compose.yml (command:).

COPY . /app
