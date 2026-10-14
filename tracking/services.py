from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import Child, Device


class DeviceNotAllowed(Exception):
    """The authenticated device is unknown, deactivated or not bound to a child."""


class UnknownChild(Exception):
    """A webhook refers to a child that does not exist."""


@dataclass(frozen=True)
class LocationResult:
    stored: bool  # False: we already had this exact update (retry from the phone)
    is_latest: bool  # False: the update is older than the location we already show


INSERT_HISTORY_SQL = """
    INSERT INTO location_updates (child_id, device_id, recorded_at, latitude, longitude, received_at)
    VALUES (%(child_id)s, %(device_id)s, %(recorded_at)s, %(latitude)s, %(longitude)s, %(received_at)s)
    ON CONFLICT (child_id, recorded_at) DO NOTHING
"""

# The WHERE on DO UPDATE is the whole "stale update" rule: the comparison and the write
# happen in one statement under the row lock, so concurrent requests cannot interleave
# and a late-arriving old point can never overwrite a newer one.
UPSERT_LAST_LOCATION_SQL = """
    INSERT INTO child_last_location (child_id, latitude, longitude, recorded_at, received_at)
    VALUES (%(child_id)s, %(latitude)s, %(longitude)s, %(recorded_at)s, %(received_at)s)
    ON CONFLICT (child_id) DO UPDATE
       SET latitude    = EXCLUDED.latitude,
           longitude   = EXCLUDED.longitude,
           recorded_at = EXCLUDED.recorded_at,
           received_at = EXCLUDED.received_at
     WHERE EXCLUDED.recorded_at > child_last_location.recorded_at
"""


def record_location(*, device_id: int, latitude: float, longitude: float, recorded_at: datetime) -> LocationResult:
    # The child is resolved from the authenticated device, never from the request body,
    # so a device cannot report coordinates for somebody else's child.
    child_id = (
        Device.objects.filter(id=device_id, is_active=True).values_list('child_id', flat=True).first()
    )
    if child_id is None:
        raise DeviceNotAllowed

    params = {
        'child_id': child_id,
        'device_id': device_id,
        'latitude': latitude,
        'longitude': longitude,
        'recorded_at': recorded_at,
        'received_at': timezone.now(),
    }
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(INSERT_HISTORY_SQL, params)
        stored = cursor.rowcount == 1
        cursor.execute(UPSERT_LAST_LOCATION_SQL, params)
        is_latest = cursor.rowcount == 1
    return LocationResult(stored=stored, is_latest=is_latest)


# Starts from parent_children, so only linked children can ever appear in the result.
# LEFT JOINs keep children that have no location and no tutor sessions yet.
# Each session is clamped to [day_start, day_end): a session that crossed midnight
# contributes only the part that belongs to today.
OVERVIEW_SQL = """
    WITH bounds AS (
        SELECT d.day AT TIME ZONE %(tz)s                      AS day_start,
               (d.day + interval '1 day') AT TIME ZONE %(tz)s AS day_end
          FROM (SELECT date_trunc('day', %(now)s AT TIME ZONE %(tz)s) AS day) d
    )
    SELECT c.id,
           c.full_name,
           l.latitude,
           l.longitude,
           l.recorded_at,
           COALESCE(t.seconds, 0)::bigint AS tutor_seconds_today
      FROM parent_children pc
      JOIN children c ON c.id = pc.child_id
     CROSS JOIN bounds b
      LEFT JOIN child_last_location l ON l.child_id = c.id
      LEFT JOIN LATERAL (
               SELECT SUM(EXTRACT(EPOCH FROM
                          LEAST(s.ended_at, b.day_end) - GREATEST(s.started_at, b.day_start))) AS seconds
                 FROM tutor_sessions s
                WHERE s.child_id = c.id
                  AND s.status = 'completed'
                  AND s.ended_at > b.day_start
                  AND s.started_at < b.day_end
           ) t ON TRUE
     WHERE pc.parent_id = %(parent_id)s
     ORDER BY c.id
"""


def get_children_overview(*, parent_id: int, now: datetime | None = None) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            OVERVIEW_SQL,
            {'parent_id': parent_id, 'now': now or timezone.now(), 'tz': settings.SCHOOL_TIME_ZONE},
        )
        rows = cursor.fetchall()

    return [
        {
            'child_id': child_id,
            'full_name': full_name,
            'last_location': None
            if recorded_at is None
            else {'latitude': latitude, 'longitude': longitude, 'recorded_at': recorded_at},
            'tutor_seconds_today': tutor_seconds,
        }
        for child_id, full_name, latitude, longitude, recorded_at, tutor_seconds in rows
    ]


# Webhooks can arrive twice or out of order ("completed" before a late "in_progress").
# - ON CONFLICT (external_id) turns a repeated event into an update of the same row.
# - "completed" is final: once there, the WHERE skips every later event, so a duplicate
#   or a stale "in_progress" cannot reopen the session or change its counted duration.
# - child_id must match, so an event can never move a session to another child.
UPSERT_TUTOR_SESSION_SQL = """
    INSERT INTO tutor_sessions (external_id, child_id, status, started_at, ended_at)
    VALUES (%(external_id)s, %(child_id)s, %(status)s, %(started_at)s, %(ended_at)s)
    ON CONFLICT (external_id) DO UPDATE
       SET status     = EXCLUDED.status,
           started_at = EXCLUDED.started_at,
           ended_at   = EXCLUDED.ended_at
     WHERE tutor_sessions.status <> 'completed'
       AND tutor_sessions.child_id = EXCLUDED.child_id
"""


def apply_tutor_session_event(
    *, external_id: str, child_id: int, status: str, started_at: datetime, ended_at: datetime | None
) -> bool:
    """Returns False when the event changed nothing (duplicate, stale or conflicting)."""
    params = {
        'external_id': external_id,
        'child_id': child_id,
        'status': status,
        'started_at': started_at,
        'ended_at': ended_at,
    }
    # Checked explicitly: Django creates foreign keys as DEFERRABLE INITIALLY DEFERRED,
    # so a bad child_id would only fail at COMMIT, outside any except block here.
    if not Child.objects.filter(id=child_id).exists():
        raise UnknownChild

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(UPSERT_TUTOR_SESSION_SQL, params)
        return cursor.rowcount == 1
