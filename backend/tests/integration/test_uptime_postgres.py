"""PostgresUptimeRepo: monitors, claiming due checks, check results and incidents."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, delete, func, select

from backend.ssl_monitor.adapters.db.tables import check_results, incidents, monitors
from backend.ssl_monitor.adapters.storage import uptime_postgres
from backend.ssl_monitor.adapters.storage.base import UptimeRepoPort
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.services.uptime_checker import run_cycle
from backend.ssl_monitor.core.uptime_models import (
    CheckResult,
    Incident,
    IncidentEvent,
    Monitor,
    MonitorState,
    MonitorTransition,
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


def _up(result: CheckResult) -> MonitorTransition:
    return MonitorTransition(result=result, state=MonitorState.UP, consecutive_failures=0,
                             consecutive_successes=1, first_failure_at=None)


def _went_down(monitor_id: int, *, seconds: int = 0, started: int = -120,
               error: str = "HTTP 503") -> MonitorTransition:
    result = _result(monitor_id, seconds=seconds, is_up=False, status_code=503, error=error)
    return MonitorTransition(result=result, state=MonitorState.DOWN, consecutive_failures=3,
                             consecutive_successes=0,
                             first_failure_at=NOW + dt.timedelta(seconds=started),
                             event=IncidentEvent.WENT_DOWN)


def _recovered(monitor_id: int, *, seconds: int = 0) -> MonitorTransition:
    return MonitorTransition(result=_result(monitor_id, seconds=seconds), state=MonitorState.UP,
                             consecutive_failures=0, consecutive_successes=2,
                             first_failure_at=None, event=IncidentEvent.RECOVERED)


def _count(pg, table) -> int:
    with pg.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _record(repo, transitions: list[MonitorTransition]) -> list[Incident]:
    """Save prepared transitions through ``record_checks`` (rules return them as-is)."""
    prepared = {id(t.result): t for t in transitions}
    return repo.record_checks([t.result for t in transitions],
                              lambda monitor, result: prepared[id(result)])


def test_repo_satisfies_port(pg):
    assert isinstance(PostgresUptimeRepo(pg), UptimeRepoPort)


def test_add_and_get_round_trip_with_defaults(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(Monitor(id=999, name="a", url="https://a.example.com",
                                     interval_seconds=300))

    assert added.id == 1
    assert added.type is MonitorType.HTTP
    assert added.state is MonitorState.UNKNOWN
    assert (added.consecutive_failures, added.consecutive_successes) == (0, 0)
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


def test_record_checks_persists_last_fields_and_transition_state(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    first_failure = NOW - dt.timedelta(seconds=60)
    result = _result(added.id, is_up=False, status_code=503, response_ms=900, error="HTTP 503")

    returned = _record(repo, [MonitorTransition(
        result=result, state=MonitorState.UNKNOWN, consecutive_failures=2,
        consecutive_successes=0, first_failure_at=first_failure)])

    after = repo.get_monitor(added.id)
    assert returned == []
    assert (after.last_checked_at, after.last_status_code, after.last_response_ms,
            after.last_error) == (NOW, 503, 900, "HTTP 503")
    assert (after.state, after.consecutive_failures, after.consecutive_successes,
            after.first_failure_at) == (MonitorState.UNKNOWN, 2, 0, first_failure)
    assert after.next_check_at == added.next_check_at
    assert after.updated_at >= added.updated_at
    assert repo.list_check_results(added.id) == [result]


def test_record_checks_clears_first_failure_and_counts_successes(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    _record(repo, [_went_down(added.id)])
    _record(repo, [_recovered(added.id, seconds=5)])

    after = repo.get_monitor(added.id)
    assert (after.state, after.consecutive_failures, after.consecutive_successes,
            after.first_failure_at) == (MonitorState.UP, 0, 2, None)


def test_record_empty_transitions_is_noop(pg):
    assert _record(PostgresUptimeRepo(pg), []) == []


def test_went_down_opens_incident(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())

    opened = _record(repo, [_went_down(added.id, started=-120, error="timeout")])

    assert opened == [Incident(id=1, monitor_id=added.id,
                               started_at=NOW - dt.timedelta(seconds=120), reason="timeout")]
    assert opened[0].is_open
    assert repo.list_incidents() == opened


def test_went_down_without_first_failure_starts_at_checked_at(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())

    [opened] = _record(repo, [replace(_went_down(added.id, seconds=7),
                                           first_failure_at=None)])

    assert opened.started_at == NOW + dt.timedelta(seconds=7)


def test_second_went_down_while_open_opens_nothing(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    [first] = _record(repo, [_went_down(added.id)])

    again = _record(repo, [_went_down(added.id, seconds=60, error="other")])

    assert again == []
    assert repo.list_incidents() == [first]
    assert len(repo.list_check_results(added.id)) == 2


def test_recovered_closes_open_incident(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    [opened] = _record(repo, [_went_down(added.id, started=-120)])

    closed = _record(repo, [_recovered(added.id, seconds=5)])

    expected = replace(opened, resolved_at=NOW + dt.timedelta(seconds=5), duration_seconds=125)
    assert closed == [expected]
    assert repo.list_incidents() == [expected]


def test_recovered_duration_floors_fractional_seconds(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    _record(repo, [_went_down(added.id, started=-10)])
    late = replace(_result(added.id), checked_at=NOW + dt.timedelta(milliseconds=999))

    [closed] = _record(repo, [replace(_recovered(added.id), result=late)])

    assert closed.duration_seconds == 10


def test_recovered_without_open_incident_is_noop(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())

    assert _record(repo, [_recovered(added.id)]) == []
    assert repo.list_incidents() == []
    assert repo.get_monitor(added.id).state is MonitorState.UP


def test_went_down_after_recovery_opens_new_incident(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    _record(repo, [_went_down(added.id, started=-120)])
    _record(repo, [_recovered(added.id)])

    [reopened] = _record(repo, [_went_down(added.id, seconds=300, started=200)])

    assert reopened.id == 2 and reopened.is_open
    assert [i.id for i in repo.list_incidents(added.id)] == [2, 1]


def test_incident_changes_return_in_transition_order(pg):
    repo = PostgresUptimeRepo(pg)
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_went_down(a.id)])

    changed = _record(repo, [_went_down(b.id), _up(_result(a.id)),
                                  _recovered(a.id, seconds=1)])

    assert [(i.monitor_id, i.is_open) for i in changed] == [(b.id, True), (a.id, False)]


def test_transitions_for_deleted_monitors_are_dropped(pg):
    repo = PostgresUptimeRepo(pg)
    kept = repo.add_monitor(_monitor("kept"))
    gone = repo.add_monitor(_monitor("gone"))
    with pg.begin() as conn:
        conn.execute(delete(monitors).where(monitors.c.id == gone.id))

    opened = _record(repo, [_went_down(gone.id), _went_down(kept.id),
                                 _up(_result(12345))])

    with pg.connect() as conn:
        stored = conn.execute(select(check_results.c.monitor_id)).scalars().all()
    assert stored == [kept.id]
    assert [i.monitor_id for i in opened] == [kept.id]
    assert repo.get_monitor(kept.id).state is MonitorState.DOWN


def test_transitions_only_for_deleted_monitors_write_nothing(pg):
    repo = PostgresUptimeRepo(pg)
    assert _record(repo, [_went_down(12345)]) == []
    assert _count(pg, check_results) == 0
    assert _count(pg, incidents) == 0


def test_record_checks_is_atomic(pg, monkeypatch):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())

    def fail(conn, transition):
        raise RuntimeError("incident step failed")

    monkeypatch.setattr(uptime_postgres, "_open_incident", fail)
    with pytest.raises(RuntimeError, match="incident step failed"):
        _record(repo, [_went_down(added.id)])

    assert _count(pg, check_results) == 0
    assert _count(pg, incidents) == 0
    assert repo.get_monitor(added.id) == added


def test_list_incidents_newest_first_with_filter_and_limit(pg):
    repo = PostgresUptimeRepo(pg)
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_went_down(a.id, started=-300)])
    _record(repo, [_recovered(a.id)])
    _record(repo, [_went_down(a.id, started=-100), _went_down(b.id, started=-200)])

    assert [(i.monitor_id, i.started_at) for i in repo.list_incidents()] == [
        (a.id, NOW - dt.timedelta(seconds=100)),
        (b.id, NOW - dt.timedelta(seconds=200)),
        (a.id, NOW - dt.timedelta(seconds=300)),
    ]
    assert [i.id for i in repo.list_incidents(a.id)] == [2, 1]
    assert [i.id for i in repo.list_incidents(limit=1)] == [2]
    assert repo.list_incidents(monitor_id=999) == []


def test_list_incidents_ties_break_on_newest_id(pg):
    repo = PostgresUptimeRepo(pg)
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_went_down(a.id), _went_down(b.id)])
    assert [i.monitor_id for i in repo.list_incidents()] == [b.id, a.id]


def test_list_check_results_newest_first_with_limit(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    other = repo.add_monitor(_monitor("other"))
    _record(repo, [_up(_result(added.id, seconds=s)) for s in (10, 30, 20)]
                       + [_up(_result(other.id, seconds=99))])

    listed = repo.list_check_results(added.id, limit=2)

    assert [r.checked_at for r in listed] == [NOW + dt.timedelta(seconds=s) for s in (30, 20)]
    assert listed[0] == _result(added.id, seconds=30)


# ── Rules run against the CURRENT stored state (pause/edit during a check wins) ──


def _down(monitor_id: int, *, seconds: int = 0) -> CheckResult:
    return _result(monitor_id, seconds=seconds, is_up=False, status_code=503, error="HTTP 503")


def test_result_for_monitor_paused_during_the_check_is_dropped(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    repo.claim_due_monitors(NOW, limit=10)
    with pg.begin() as conn:
        conn.execute(monitors.update().values(is_paused=True))

    assert repo.record_checks([_down(added.id)], evaluate) == []
    assert _count(pg, check_results) == 0
    assert repo.get_monitor(added.id).consecutive_failures == 0


def test_rules_see_state_changed_after_the_claim(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor())
    repo.record_checks([_down(added.id), _down(added.id, seconds=60)], evaluate)
    with pg.begin() as conn:  # e.g. the monitor was edited: counters reset
        conn.execute(monitors.update().values(consecutive_failures=0, first_failure_at=None))

    opened = repo.record_checks([_down(added.id, seconds=120)], evaluate)

    assert opened == []
    assert repo.get_monitor(added.id).consecutive_failures == 1


def test_full_lifecycle_through_run_cycle_against_postgres(pg):
    repo = PostgresUptimeRepo(pg)
    added = repo.add_monitor(_monitor(due_in=0))
    outcomes = iter([False, False, False, True])

    async def probe(monitor, checked_at):
        is_up = next(outcomes)
        return CheckResult(monitor.id, checked_at, is_up=is_up,
                           error=None if is_up else "HTTP 503")

    summaries = []
    for cycle in range(4):
        now = NOW + dt.timedelta(seconds=60 * cycle)
        summaries.append(asyncio.run(run_cycle(
            repo, probe, now=now, max_concurrency=5, clock=lambda now=now: now)))

    assert [(s.went_down, s.recovered) for s in summaries] == [(0, 0), (0, 0), (1, 0), (0, 1)]
    [incident] = repo.list_incidents(added.id)
    assert incident.started_at == NOW
    assert incident.resolved_at == NOW + dt.timedelta(seconds=180)
    assert (incident.duration_seconds, incident.reason) == (180, "HTTP 503")
    assert repo.get_monitor(added.id).state is MonitorState.UP
