# Python
import os
import time
import platform
from datetime import timedelta

# Django
import django
from django.db import connection
from django.utils import timezone
from django.template.defaultfilters import filesizeformat

# Models
from app.models.mask import Mask
from app.models.thread import Thread
from app.models.media import ThreadFile
from app.models.reaction_relation import ReactionRelation
from app.models.report import Report
from app.models.momentum_log import MomentumLog
from app.models.purge_log import PurgeLog

# Libs
from app.methods import presence

# Worker start time: uptime is PER GUNICORN WORKER (max_requests recycling
# restarts workers on purpose — a short uptime here is normal, not a crash).
_WORKER_STARTED = time.time()


def _proc_kb(path: str, field: str):
    """Read a `Field:  <n> kB` line from a /proc file. None off-Linux."""
    try:
        with open(path) as handle:
            for line in handle:
                if line.startswith(field + ":"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _process_rss_bytes():
    kb = _proc_kb("/proc/self/status", "VmRSS")
    return kb * 1024 if kb is not None else None


def _system_memory():
    """(total, available, used_percent) from /proc/meminfo, or Nones."""
    total_kb = _proc_kb("/proc/meminfo", "MemTotal")
    avail_kb = _proc_kb("/proc/meminfo", "MemAvailable")
    if not total_kb or avail_kb is None:
        return None, None, None
    used = total_kb - avail_kb
    return total_kb * 1024, avail_kb * 1024, round(used * 100 / total_kb)


def _db_latency_ms() -> float:
    started = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return round((time.monotonic() - started) * 1000, 1)


def _db_size():
    """Pretty database size (PostgreSQL only; None elsewhere)."""
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_database_size(current_database())")
        return filesizeformat(cursor.fetchone()[0])


def _uptime_human(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds % 60}s"


def collect_metrics() -> dict:
    """One snapshot of everything the owner dashboard shows.

    Computed ON DEMAND when the admin page is opened — a handful of indexed
    COUNTs, two tiny /proc reads and two trivial SQL statements. Nothing here
    runs in the request path of the public API or on a schedule.
    """
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    rss = _process_rss_bytes()
    sys_total, sys_available, sys_used_percent = _system_memory()
    try:
        load_1m = round(os.getloadavg()[0], 2)
    except OSError:
        load_1m = None

    return {
        "generated_at": now,
        "process": {
            "rss": filesizeformat(rss) if rss is not None else "n/a",
            "uptime": _uptime_human(time.time() - _WORKER_STARTED),
            "pid": os.getpid(),
            "python": platform.python_version(),
            "django": django.get_version(),
            "git_sha": (os.environ.get("GIT_SHA") or "")[:12] or "n/a",
        },
        "system": {
            "memory_total": filesizeformat(sys_total) if sys_total else "n/a",
            "memory_available": (
                filesizeformat(sys_available) if sys_available else "n/a"),
            "memory_used_percent": sys_used_percent,
            "load_1m": load_1m,
            "cpu_count": os.cpu_count(),
        },
        "activity": {
            # None -> the cache backend can't be introspected ("n/a").
            "online_now": presence.online_count(),
            "presence_ttl": presence.PRESENCE_TTL_SECONDS,
            "masks_total": Mask.objects.filter(is_active=True).count(),
            "threads_24h": Thread.objects.filter(
                is_active=True, create_at__gte=day_ago).count(),
            "reactions_24h": ReactionRelation.objects.filter(
                is_active=True, create_at__gte=day_ago).count(),
        },
        "content": {
            "threads": Thread.objects.filter(
                is_active=True, sub__isnull=True).count(),
            "replies": Thread.objects.filter(
                is_active=True, sub__isnull=False).count(),
            "reactions": ReactionRelation.objects.filter(
                is_active=True).count(),
            "media_files": ThreadFile.objects.filter(is_active=True).count(),
            "open_reports": Report.objects.filter(is_active=True).count(),
            "soft_deleted": Thread.objects.filter(is_active=False).count()
            + ThreadFile.objects.filter(is_active=False).count(),
        },
        "database": {
            "latency_ms": _db_latency_ms(),
            "size": _db_size() or "n/a",
            "vendor": connection.vendor,
        },
        "jobs": {
            "momentum": MomentumLog.objects.order_by("-create_at").first(),
            "purge": PurgeLog.objects.order_by("-create_at").first(),
        },
    }
