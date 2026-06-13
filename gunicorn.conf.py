# Gunicorn — configuration for memory-constrained, always-on hosting
# (small cloud instances / Raspberry Pi).
#
# Usage (production): in docker-compose change the web service command to
#   gunicorn core.wsgi:application -c gunicorn.conf.py -b 0.0.0.0:8000
# (in dev, runserver with autoreload is kept).
#
# Rationale:
#  - I/O-bound load (Postgres + lightweight encryption): gthread worker with few
#    PROCESSES (RAM: each process ≈ one copy of Django) and several THREADS
#    (cheap, share memory). 2 processes × 4 threads = 8 concurrent
#    requests.
#  - preload_app: load the app ONCE in the master, then fork. Workers share
#    the loaded code/data via copy-on-write, so total real memory (PSS) is
#    roughly halved vs forking before load. Measured here: ~98 MB PSS without
#    preload → ~49 MB with it (2w×4t). Safe because the WSGI app opens no DB
#    connection at import time (Django connects lazily, per worker/thread).
#  - max_requests + jitter: recycles workers periodically (contains memory
#    leaks in long-lived processes — key with little RAM).
import multiprocessing
import os

cores = multiprocessing.cpu_count()

# Few processes (limited by RAM), never more than the available cores.
workers = int(os.environ.get("GUNICORN_WORKERS", min(2, cores)))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))

# Load app before forking → copy-on-write sharing across workers (lower RAM).
preload_app = True

timeout = 60
graceful_timeout = 30
keepalive = 5

# Anti-leak recycling (jitter so they don't all recycle at once).
max_requests = 500
max_requests_jitter = 50

# Sparse logging (I/O is costly on Raspberry SD cards).
loglevel = "warning"
accesslog = None
errorlog = "-"
