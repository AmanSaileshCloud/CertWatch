"""Report queries (incidents_between, check_stats) against a real PostgreSQL."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import insert

from backend.ssl_monitor.adapters.db.tables import (
    check_results,
    check_results_hourly,
    incidents,
    monitors,
)
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
from backend.ssl_monitor.core.reports import CheckStats

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc
TZ = "Asia/Kolkata"


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


def _monitor(pg) -> int:
    with pg.begin() as conn:
        return conn.execute(insert(monitors).values(
            name="site", url="https://site.example.com", interval_seconds=60
        ).returning(monitors.c.id)).scalar_one()


def test_incidents_between_returns_only_overlapping_outages(pg) -> None:
    mid = _monitor(pg)
    with pg.begin() as conn:
        conn.execute(insert(incidents), [
            {"monitor_id": mid, "started_at": _utc(2026, 10, 1), "resolved_at": _utc(2026, 10, 2)},
            {"monitor_id": mid, "started_at": _utc(2026, 10, 31, 23),
             "resolved_at": _utc(2026, 11, 1, 1)},
            {"monitor_id": mid, "started_at": _utc(2026, 11, 3), "resolved_at": None},
            {"monitor_id": mid, "started_at": _utc(2026, 11, 20), "resolved_at": _utc(2026, 11, 21)},
        ])
    found = PostgresUptimeRepo(pg).incidents_between(_utc(2026, 11, 1), _utc(2026, 11, 10))
    assert [i.started_at for i in found] == [_utc(2026, 10, 31, 23), _utc(2026, 11, 3)]


def test_check_stats_combine_hourly_summaries_and_newer_raw_results(pg) -> None:
    mid = _monitor(pg)
    with pg.begin() as conn:
        # Summarised: 2 Nov 04:00 UTC (09:30 IST, 2 Nov) — 10 checks, 9 up, avg 100 ms.
        conn.execute(insert(check_results_hourly).values(
            monitor_id=mid, hour_start=_utc(2026, 11, 2, 4), total_checks=10, up_checks=9,
            avg_response_ms=100.0, min_response_ms=50, max_response_ms=200))
        conn.execute(insert(check_results), [
            # Older than the last summarised hour: already in the summary, must be ignored.
            {"monitor_id": mid, "checked_at": _utc(2026, 11, 2, 4, 30), "is_up": True,
             "response_ms": 9999},
            # Not summarised yet: 2 Nov 20:00 UTC = 3 Nov 01:30 IST → counts for 3 Nov.
            {"monitor_id": mid, "checked_at": _utc(2026, 11, 2, 20), "is_up": False,
             "response_ms": None},
            {"monitor_id": mid, "checked_at": _utc(2026, 11, 2, 20, 1), "is_up": True,
             "response_ms": 300},
        ])
    stats = PostgresUptimeRepo(pg).check_stats(
        _utc(2026, 11, 1), _utc(2026, 11, 5), tz=TZ, unit="day")
    assert stats == {
        (mid, dt.date(2026, 11, 2)): CheckStats(10, 9, 1000.0, 10),
        (mid, dt.date(2026, 11, 3)): CheckStats(2, 1, 300.0, 1),
    }


def test_check_stats_group_by_month(pg) -> None:
    mid = _monitor(pg)
    with pg.begin() as conn:
        conn.execute(insert(check_results), [
            {"monitor_id": mid, "checked_at": _utc(2026, 10, 15), "is_up": True, "response_ms": 100},
            {"monitor_id": mid, "checked_at": _utc(2026, 11, 15), "is_up": True, "response_ms": 300},
            {"monitor_id": mid, "checked_at": _utc(2026, 11, 16), "is_up": True, "response_ms": 500},
        ])
    stats = PostgresUptimeRepo(pg).check_stats(
        _utc(2026, 10, 1), _utc(2026, 12, 1), tz=TZ, unit="month")
    assert stats[(mid, dt.date(2026, 10, 1))].avg_response_ms == 100
    assert stats[(mid, dt.date(2026, 11, 1))].avg_response_ms == 400
