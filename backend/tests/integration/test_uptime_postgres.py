"""PostgresUptimeRepo: monitors, claiming due checks, and check results."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, delete, select, update

from backend.ssl_monitor.adapters.db.tables import check_results, monitors
from backend.ssl_monitor.adapters.storage.base import UptimeRepoPort
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
from backend.ssl_monitor.core.uptime_models import (
    CheckResult,
    Monitor,
    MonitorState,
    MonitorType,
)

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _monitor(name: str = "site", *, due_in: int = -1, interval: int = 60, **overrides) -> Monitor:
    return Monitor(
        id=0,
        name=name,
        url=f"https://{name}.example.com",
        interval_seconds=interval,
        next_check_at=NOW + dt.timedelta(seconds=due_in),
        **overrides,
    )


def _result(monitor_id: int, *, seconds: int = 0, **overrides) -> CheckResult:
    values = {"is_up": True, "status_code": 200, "response_ms": 120} | overrides
    return CheckResult(monitor_id=monitor_id, checked_at=NOW + dt.timedelta(seconds=seconds),
                       **values)


def test_repo_satisfies_port(pg):
    assert isinstance(PostgresUptimeRepo(pg), UptimeRepoPort)


def test_add_and_get_round_trip_with_defaults(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(Monitor(id=999, name="a", url="https://a.example.com",
                                     interval_seconds=300))

    assert added.id == 1
    assert added.type is MonitorType.HTTP
    assert added.state is MonitorState.UNKNOWN
    assert added.timeout_seconds == 10
    assert added.expected_status == 200
    assert added.next_check_at is not None and added.next_check_at.tzinfo is not None
    assert added.created_at is not None
    assert repo.get_monitor(added.id) == added


def test_get_missing_monitor_returns_none(pg):
    assert PostgresUptimeRepo(pg).get_monitor(42) is None


def test_add_keeps_explicit_next_check_at_and_config(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor(due_in=30, keyword="ok", expected_status=204,
                                      timeout_seconds=5, is_paused=True))
    assert added.next_check_at == NOW + dt.timedelta(seconds=30)
    assert (added.keyword, added.expected_status, added.timeout_seconds, added.is_paused) == (
        "ok", 204, 5, True)


def test_claim_returns_only_due_unpaused_oldest_first(pg):
    repo = PostgresUptimeRepo(pg)
    newer = repo.add_monitor(_monitor("newer", due_in=-10))
    older = repo.add_monitor(_monitor("older", due_in=-100))
    repo.add_monitor(_monitor("future", due_in=60))
    repo.add_monitor(_monitor("paused", due_in=-500, is_paused=True))

    claimed = repo.claim_due_monitors(NOW, limit=10)

    assert [m.id for m in claimed] == [older.id, newer.id]
    assert claimed[0].next_check_at == older.next_check_at  # old value returned


def test_claim_honours_limit(pg):
    repo = PostgresUptimeRepo(pg)
    ids = [repo.add_monitor(_monitor(f"m{i}", due_in=-100 + i)).id for i in range(5)]
    assert [m.id for m in repo.claim_due_monitors(NOW, limit=2)] == ids[:2]
    assert [m.id for m in repo.claim_due_monitors(NOW, limit=10)] == ids[2:]


@pytest.mark.parametrize("interval", [60, 300, 600])
def test_claim_keeps_cadence_anchored_to_due_time(pg, interval):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor(interval=interval, due_in=-1))
    repo.claim_due_monitors(NOW, limit=10)
    due_at = NOW - dt.timedelta(seconds=1)
    assert repo.get_monitor(added.id).next_check_at == due_at + dt.timedelta(seconds=interval)


def test_claim_far_behind_schedule_restarts_from_now(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor(interval=60, due_in=-600))
    repo.claim_due_monitors(NOW, limit=10)
    assert repo.get_monitor(added.id).next_check_at == NOW + dt.timedelta(seconds=60)


def test_second_claim_at_same_time_returns_nothing(pg):
    repo = PostgresUptimeRepo(pg)
    repo.add_monitor(_monitor())
    assert len(repo.claim_due_monitors(NOW, limit=10)) == 1
    assert repo.claim_due_monitors(NOW, limit=10) == []


def test_claim_with_nothing_due_returns_empty(pg):
    assert PostgresUptimeRepo(pg).claim_due_monitors(NOW, limit=10) == []


def test_claim_skips_rows_locked_by_another_worker(pg):
    repo = PostgresUptimeRepo(pg)
    locked = repo.add_monitor(_monitor("locked", due_in=-100))
    free = repo.add_monitor(_monitor("free", due_in=-10))
    # A second engine with a lock timeout, so a regression to plain FOR UPDATE
    # fails fast instead of hanging the test run.
    other = create_engine(pg.url, connect_args={"options": "-c lock_timeout=2000"})
    try:
        with pg.connect() as holder, holder.begin():
            holder.execute(select(monitors.c.id).where(monitors.c.id == locked.id)
                           .with_for_update())
            claimed = PostgresUptimeRepo(other).claim_due_monitors(NOW, limit=10)
    finally:
        other.dispose()

    assert [m.id for m in claimed] == [free.id]
    assert repo.get_monitor(locked.id).next_check_at == locked.next_check_at


def test_save_check_results_updates_last_fields_only(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    with pg.begin() as conn:
        conn.execute(update(monitors).where(monitors.c.id == added.id)
                     .values(state="down", consecutive_failures=2))
    before = repo.get_monitor(added.id)

    repo.save_check_results([_result(added.id, is_up=False, status_code=503,
                                     response_ms=900, error="HTTP 503")])

    after = repo.get_monitor(added.id)
    assert (after.last_checked_at, after.last_status_code, after.last_response_ms,
            after.last_error) == (NOW, 503, 900, "HTTP 503")
    assert after.state is MonitorState.DOWN
    assert after.consecutive_failures == 2
    assert after.first_failure_at == before.first_failure_at
    assert after.next_check_at == before.next_check_at
    assert after.updated_at >= before.updated_at
    assert len(repo.list_check_results(added.id)) == 1


def test_save_empty_results_is_noop(pg):
    PostgresUptimeRepo(pg).save_check_results([])


def test_results_for_deleted_monitor_are_dropped(pg):
    repo = PostgresUptimeRepo(pg)
    kept = repo.add_monitor(_monitor("kept"))
    gone = repo.add_monitor(_monitor("gone"))
    with pg.begin() as conn:
        conn.execute(delete(monitors).where(monitors.c.id == gone.id))

    repo.save_check_results([_result(gone.id), _result(kept.id)])

    with pg.connect() as conn:
        stored = conn.execute(select(check_results.c.monitor_id)).scalars().all()
    assert stored == [kept.id]
    assert repo.get_monitor(kept.id).last_checked_at == NOW


def test_results_only_for_deleted_monitors_write_nothing(pg):
    repo = PostgresUptimeRepo(pg)
    repo.save_check_results([_result(12345)])
    assert repo.list_check_results(12345) == []


def test_list_check_results_newest_first_with_limit(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    other = repo.add_monitor(_monitor("other"))
    repo.save_check_results([_result(added.id, seconds=s) for s in (10, 30, 20)]
                            + [_result(other.id, seconds=99)])

    listed = repo.list_check_results(added.id, limit=2)

    assert [r.checked_at for r in listed] == [NOW + dt.timedelta(seconds=s) for s in (30, 20)]
    assert listed[0] == _result(added.id, seconds=30)
