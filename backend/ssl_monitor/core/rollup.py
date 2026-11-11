"""Which hours to summarise and which raw check results may be deleted.

Pure rules — no I/O, no clock reads. The rollup job turns raw ``check_results``
rows into one ``check_results_hourly`` row per monitor per hour, then deletes
raw rows older than the retention period. Only *finished* hours are summarised,
and nothing is ever deleted before it has been summarised.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Big catch-up runs (e.g. the first run on an existing database) are summarised
# one day at a time so no single transaction grows too large.
CHUNK = timedelta(days=1)


def floor_hour(moment: datetime) -> datetime:
    """Start of the UTC hour containing ``moment``. Converting first matters: the
    database may hand back times in IST (+05:30), whose hours start at :30 UTC."""
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def rollup_window(
    last_rolled_hour: datetime | None, earliest_result: datetime | None, now: datetime
) -> tuple[datetime, datetime] | None:
    """The [start, end) span of finished hours to (re)summarise, or None.

    Starts at the last hour already summarised (re-doing it is harmless and
    picks up any result written just after it was first summarised), or at the
    oldest raw result on a first run. Ends at the start of the current hour.
    """
    end = floor_hour(now)
    if last_rolled_hour is not None:
        start = floor_hour(last_rolled_hour)
    elif earliest_result is not None:
        start = floor_hour(earliest_result)
    else:
        return None
    return (start, end) if start < end else None


def chunks(start: datetime, end: datetime, size: timedelta = CHUNK) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into consecutive spans of at most ``size``."""
    spans = []
    while start < end:
        stop = min(start + size, end)
        spans.append((start, stop))
        start = stop
    return spans


def deletion_cutoff(
    now: datetime, retention_days: int, summarised_until: datetime | None
) -> datetime | None:
    """Raw results older than this may be deleted, or None to delete nothing.

    Normally ``now - retention_days``; never later than the end of the hours
    already summarised, so no raw data is lost before it is in the hourly table.
    """
    if summarised_until is None:
        return None
    return min(now - timedelta(days=retention_days), summarised_until)
