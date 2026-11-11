"""Hourly rollup + cleanup against a real PostgreSQL."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, func, insert, select

from backend.ssl_monitor.adapters.db.tables import check_results, check_results_hourly, monitors
from backend.ssl_monitor.adapters.storage.rollup_postgres import PostgresRollupRepo
from backend.ssl_monitor.services.uptime_rollup import run_rollup

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 30, tzinfo=UTC)


def _monitor(pg) -> int:
    with pg.begin() as conn:
        return conn.execute(
            insert(monitors).values(name="site", url="https://site.example.com",
                                    interval_seconds=60).returning(monitors.c.id)
        ).scalar_one()


def _results(pg, monitor_id: int, *rows: tuple[dt.datetime, bool, int]) -> None:
    with pg.begin() as conn:
        conn.execute(insert(check_results), [
            {"monitor_id": monitor_id, "checked_at": at, "is_up": up, "response_ms": ms}
            for at, up, ms in rows
        ])


def _hourly(pg) -> list[tuple]:
    with pg.connect() as conn:
        return [tuple(r) for r in conn.execute(select(
            check_results_hourly.c.hour_start, check_results_hourly.c.total_checks,
            check_results_hourly.c.up_checks, check_results_hourly.c.min_response_ms,
            check_results_hourly.c.max_response_ms,
        ).order_by(check_results_hourly.c.hour_start))]


def _raw_count(pg) -> int:
    with pg.connect() as conn:
        return conn.execute(select(func.count()).select_from(check_results)).scalar_one()


def test_summarises_finished_hours_and_skips_the_current_one(pg) -> None:
    mid = _monitor(pg)
    hour = dt.datetime(2026, 11, 11, 10, tzinfo=UTC)
    _results(pg, mid,
             (hour + dt.timedelta(minutes=1), True, 100),
             (hour + dt.timedelta(minutes=2), False, 300),
             (hour + dt.timedelta(hours=1, minutes=5), True, 50),
             (NOW, True, 70))  # current hour: not finished yet
    run_rollup(PostgresRollupRepo(pg), now=NOW, retention_days=30)
    assert _hourly(pg) == [(hour, 2, 1, 100, 300), (hour + dt.timedelta(hours=1), 1, 1, 50, 50)]


def test_hours_are_cut_in_utc_even_when_the_session_is_in_ist(pg) -> None:
    mid = _monitor(pg)
    _results(pg, mid, (dt.datetime(2026, 11, 11, 10, 15, tzinfo=UTC), True, 10))
    ist = create_engine(pg.url, connect_args={"options": "-c timezone=Asia/Kolkata"})
    try:
        run_rollup(PostgresRollupRepo(ist), now=NOW, retention_days=30)
    finally:
        ist.dispose()
    assert [row[0] for row in _hourly(pg)] == [dt.datetime(2026, 11, 11, 10, tzinfo=UTC)]


def test_running_twice_gives_the_same_numbers(pg) -> None:
    mid = _monitor(pg)
    hour = dt.datetime(2026, 11, 11, 9, tzinfo=UTC)
    _results(pg, mid, (hour, True, 10), (hour + dt.timedelta(minutes=30), True, 20))
    repo = PostgresRollupRepo(pg)
    run_rollup(repo, now=NOW, retention_days=30)
    first = _hourly(pg)
    run_rollup(repo, now=NOW, retention_days=30)
    assert _hourly(pg) == first


def test_old_raw_results_are_deleted_after_being_summarised(pg) -> None:
    mid = _monitor(pg)
    old = NOW - dt.timedelta(days=31)
    recent = NOW - dt.timedelta(days=2)
    _results(pg, mid, (old, True, 10), (old + dt.timedelta(minutes=1), False, 20),
             (recent, True, 30))
    summary = run_rollup(PostgresRollupRepo(pg), now=NOW, retention_days=30, delete_batch=1)
    assert summary.deleted == 2
    assert _raw_count(pg) == 1
    old_hour = old.replace(minute=0, second=0, microsecond=0)
    assert (old_hour, 2, 1, 10, 20) in _hourly(pg)  # the summary of the deleted rows stays
