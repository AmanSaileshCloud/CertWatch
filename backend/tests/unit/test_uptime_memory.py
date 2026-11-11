"""MemoryUptimeRepo: same behaviour as the Postgres adapter, without a database."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.storage.base import UptimeRepoPort
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.core.uptime_models import (
    CheckResult,
    Monitor,
    MonitorState,
    MonitorType,
)

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


def test_repo_satisfies_port():
    assert isinstance(MemoryUptimeRepo(), UptimeRepoPort)


def test_add_assigns_incrementing_ids_and_defaults():
    repo = MemoryUptimeRepo()
    first = repo.add_monitor(Monitor(id=999, name="a", url="https://a", interval_seconds=60))
    second = repo.add_monitor(_monitor("b"))

    assert (first.id, second.id) == (1, 2)
    assert first.type is MonitorType.HTTP and first.state is MonitorState.UNKNOWN
    assert first.next_check_at is not None and first.next_check_at.tzinfo is not None
    assert repo.get_monitor(1) == first
    assert repo.get_monitor(3) is None


def test_claim_returns_only_due_unpaused_oldest_first():
    repo = MemoryUptimeRepo()
    newer = repo.add_monitor(_monitor("newer", due_in=-10))
    older = repo.add_monitor(_monitor("older", due_in=-100))
    repo.add_monitor(_monitor("future", due_in=60))
    repo.add_monitor(_monitor("paused", due_in=-500, is_paused=True))

    claimed = repo.claim_due_monitors(NOW, limit=10)

    assert [m.id for m in claimed] == [older.id, newer.id]
    assert claimed[0].next_check_at == older.next_check_at


def test_claim_honours_limit():
    repo = MemoryUptimeRepo()
    ids = [repo.add_monitor(_monitor(f"m{i}", due_in=-100 + i)).id for i in range(5)]
    assert [m.id for m in repo.claim_due_monitors(NOW, limit=2)] == ids[:2]
    assert [m.id for m in repo.claim_due_monitors(NOW, limit=10)] == ids[2:]


@pytest.mark.parametrize("interval", [60, 300, 600])
def test_claim_keeps_cadence_anchored_to_due_time(interval):
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor(interval=interval, due_in=-1))
    repo.claim_due_monitors(NOW, limit=10)
    due_at = NOW - dt.timedelta(seconds=1)
    assert repo.get_monitor(added.id).next_check_at == due_at + dt.timedelta(seconds=interval)


def test_claim_far_behind_schedule_restarts_from_now():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor(interval=60, due_in=-600))
    repo.claim_due_monitors(NOW, limit=10)
    assert repo.get_monitor(added.id).next_check_at == NOW + dt.timedelta(seconds=60)


def test_second_claim_at_same_time_returns_nothing():
    repo = MemoryUptimeRepo()
    repo.add_monitor(_monitor())
    assert len(repo.claim_due_monitors(NOW, limit=10)) == 1
    assert repo.claim_due_monitors(NOW, limit=10) == []


def test_save_check_results_updates_last_fields_only():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())

    repo.save_check_results([_result(added.id, is_up=False, status_code=503,
                                     response_ms=900, error="HTTP 503")])

    after = repo.get_monitor(added.id)
    assert (after.last_checked_at, after.last_status_code, after.last_response_ms,
            after.last_error) == (NOW, 503, 900, "HTTP 503")
    assert after.state is MonitorState.UNKNOWN
    assert after.consecutive_failures == 0
    assert after.next_check_at == added.next_check_at


def test_results_for_unknown_monitor_are_dropped():
    repo = MemoryUptimeRepo()
    kept = repo.add_monitor(_monitor())
    repo.save_check_results([_result(404), _result(kept.id)])
    assert repo.list_check_results(404) == []
    assert repo.list_check_results(kept.id) == [_result(kept.id)]


def test_list_check_results_newest_first_with_limit():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    repo.save_check_results([_result(added.id, seconds=s) for s in (10, 30, 20)])

    listed = repo.list_check_results(added.id, limit=2)

    assert [r.checked_at for r in listed] == [NOW + dt.timedelta(seconds=s) for s in (30, 20)]
