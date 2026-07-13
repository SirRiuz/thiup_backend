# Python
# Libs
import maxminddb
from django.conf import settings

# ONE reader for the whole process, opened at import time. Opening the .mmdb
# per request cost a file open + metadata scan + mmap/munmap on EVERY request;
# maxminddb readers are thread-safe, and with gunicorn --preload the import
# happens in the master so recycled workers share the mapping (CoW).
# Raw maxminddb (not the geoip2 wrapper): only the local-database lookup is
# needed, and .get() returns the record for ANY edition — swapping the file
# for the much smaller GeoLite2-Country needs no code change.
_READER = maxminddb.open_database(settings.GEOLITE_DIR)


def get_country(address) -> str:
    """Get the country name of the user mask"""
    record = _READER.get(address)
    if record is None:
        return "Unknow"
    return (record.get("country") or {}).get("iso_code")
