"""Daily partitions for location_updates.

Retention is done by dropping whole partitions: at ~115M rows a day a DELETE would
bloat the table and fight with inserts, while DROP TABLE is instant.
"""

import re
from datetime import date, datetime, time, timedelta, timezone

from django.conf import settings
from django.db import connection

DAYS_AHEAD = 2
_PARTITION_RE = re.compile(r'^location_updates_(\d{8})$')


def rotate_partitions(today: date | None = None) -> None:
    """Create partitions for the retention window and drop the ones that fell out of it."""
    today = today or datetime.now(timezone.utc).date()
    oldest = today - timedelta(days=settings.LOCATION_RETENTION_DAYS)

    with connection.cursor() as cursor:
        day = oldest
        while day <= today + timedelta(days=DAYS_AHEAD):
            # Values come from date objects, not user input, so formatting them in is safe.
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS location_updates_{day:%Y%m%d} "
                f"PARTITION OF location_updates "
                f"FOR VALUES FROM ('{_utc_midnight(day)}') TO ('{_utc_midnight(day + timedelta(days=1))}')"
            )
            day += timedelta(days=1)

        cursor.execute(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'location_updates'::regclass"
        )
        for (name,) in cursor.fetchall():
            match = _PARTITION_RE.match(name)
            if match and datetime.strptime(match.group(1), '%Y%m%d').date() < oldest:
                cursor.execute(f'DROP TABLE {name}')


def _utc_midnight(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=timezone.utc).isoformat()
