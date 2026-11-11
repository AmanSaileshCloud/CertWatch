"""Uptime check-cycle service tests — in-memory repo + fake probes, no network."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor
from backend.ssl_monitor.services.uptime_checker import CycleSummary, run_cycle

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _add(repo: MemoryUptimeRepo, name: str, *, due_in: int = 0, paused: bool = False) -> Monitor:
    return repo.add_monitor(
        Monitor(
            id=0,
            name=name,
            url=f"https://{name}.test",
            interval_seconds=60,
            is_paused=paused,
            next_check_at=NOW + timedelta(seconds=due_in),
        )
    )


def _result(monitor: Monitor, now: datetime, *, up: bool) -> CheckResult:
    return CheckResult(
        monitor_id=monitor.id,
        checked_at=now,
        is_up=up,
        status_code=200 if up else 503,
        response_ms=12,
    )


class RecordingProbe:
    """Fake probe: everything is up unless its name is in ``down``."""

    def __init__(self, down: set[str] = frozenset()) -> None:
        self.down = down
        self.seen: list[str] = []

    async def __call__(self, monitor: Monitor, now: datetime) -> CheckResult:
        self.seen.append(monitor.name)
        return _result(monitor, now, up=monitor.name not in self.down)


def _run(repo, probe, *, now: datetime = NOW, max_concurrency: int = 10) -> CycleSummary:
    return asyncio.run(run_cycle(repo, probe, now=now, max_concurrency=max_concurrency))


def test_only_due_unpaused_monitors_are_probed():
    repo = MemoryUptimeRepo()
    _add(repo, "due")
    _add(repo, "later", due_in=30)
    _add(repo, "paused", paused=True)
    probe = RecordingProbe()

    _run(repo, probe)

    assert probe.seen == ["due"]


def test_results_saved_and_summary_counts_up_and_down():
    repo = MemoryUptimeRepo()
    ok = _add(repo, "ok")
    bad = _add(repo, "bad")

    summary = _run(repo, RecordingProbe(down={"bad"}))

    assert summary == CycleSummary(checked_at=NOW, checked=2, up=1, down=1)
    assert repo.list_check_results(ok.id)[0].is_up is True
    assert repo.list_check_results(bad.id)[0].is_up is False
    assert repo.get_monitor(bad.id).last_status_code == 503


def test_immediate_second_cycle_checks_nothing():
    repo = MemoryUptimeRepo()
    _add(repo, "a")
    _add(repo, "b")
    probe = RecordingProbe()

    _run(repo, probe)
    second = _run(repo, probe, now=NOW + timedelta(seconds=1))

    assert second.checked == 0
    assert len(probe.seen) == 2


def test_concurrency_never_exceeds_limit():
    repo = MemoryUptimeRepo()
    for i in range(12):
        _add(repo, f"site{i}")
    in_flight = 0
    peak = 0

    async def slow_probe(monitor: Monitor, now: datetime) -> CheckResult:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _result(monitor, now, up=True)

    summary = _run(repo, slow_probe, max_concurrency=3)

    assert summary.checked == 12
    assert peak == 3


def test_crashing_probe_becomes_down_result_and_others_still_saved():
    repo = MemoryUptimeRepo()
    boom = _add(repo, "boom")
    fine = _add(repo, "fine")

    async def probe(monitor: Monitor, now: datetime) -> CheckResult:
        if monitor.name == "boom":
            raise RuntimeError("kaboom")
        return _result(monitor, now, up=True)

    summary = _run(repo, probe)

    assert (summary.up, summary.down) == (1, 1)
    crashed = repo.list_check_results(boom.id)[0]
    assert crashed.is_up is False
    assert crashed.error == "Checker error: RuntimeError"
    assert repo.list_check_results(fine.id)[0].is_up is True


def test_no_due_monitors_gives_zero_summary_without_saving():
    class NothingDueRepo(MemoryUptimeRepo):
        def save_check_results(self, results):
            raise AssertionError("save should not be called")

    summary = _run(NothingDueRepo(), RecordingProbe())

    assert summary == CycleSummary(checked_at=NOW, checked=0, up=0, down=0)


def test_results_are_stamped_when_each_check_starts_not_when_cycle_started():
    repo = MemoryUptimeRepo()
    first, second = _add(repo, "a"), _add(repo, "b")
    ticks = iter([NOW + timedelta(seconds=5), NOW + timedelta(seconds=40)])

    asyncio.run(run_cycle(repo, RecordingProbe(), now=NOW, max_concurrency=1,
                          clock=lambda: next(ticks)))

    stamped = {repo.list_check_results(m.id)[0].checked_at for m in (first, second)}
    assert stamped == {NOW + timedelta(seconds=5), NOW + timedelta(seconds=40)}


def test_crash_result_carries_the_worker_region():
    repo = MemoryUptimeRepo()
    boom = _add(repo, "boom")

    async def probe(monitor: Monitor, now: datetime) -> CheckResult:
        raise RuntimeError("kaboom")

    asyncio.run(run_cycle(repo, probe, now=NOW, max_concurrency=1, region="ap-south-1"))

    assert repo.list_check_results(boom.id)[0].region == "ap-south-1"
