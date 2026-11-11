"""Uptime check-cycle service tests — in-memory repo + fake probes, no network."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor, MonitorState
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
        error=None if up else "HTTP 503",
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
        def record_checks(self, results, rules):
            raise AssertionError("record_checks should not be called")

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


# ── Incidents: consecutive-failure rule end to end ──────────────────────────

INTERVAL = timedelta(seconds=60)
CHECK_DELAY = timedelta(seconds=1)


def _check_time(cycle: int) -> datetime:
    """When the (single) check in cycle ``cycle`` is stamped — distinct per cycle."""
    return NOW + cycle * INTERVAL + CHECK_DELAY


def _run_nth(repo, probe, cycle: int, **thresholds) -> CycleSummary:
    """Run cycle number ``cycle`` (0-based); each is one interval later, so monitors are due."""
    return asyncio.run(
        run_cycle(
            repo,
            probe,
            now=NOW + cycle * INTERVAL,
            max_concurrency=10,
            clock=lambda: _check_time(cycle),
            **thresholds,
        )
    )


def test_three_failures_open_one_incident_and_recovery_closes_it():
    repo = MemoryUptimeRepo()
    site = _add(repo, "site")
    probe = RecordingProbe(down={"site"})

    for cycle in (0, 1):
        summary = _run_nth(repo, probe, cycle)
        assert (summary.went_down, summary.incidents) == (0, ())
        assert repo.list_incidents(site.id) == []
        assert repo.get_monitor(site.id).state is MonitorState.UNKNOWN

    third = _run_nth(repo, probe, 2)

    assert third.went_down == 1 and third.recovered == 0
    [incident] = repo.list_incidents(site.id)
    assert incident.is_open
    assert incident.started_at == _check_time(0)
    assert incident.reason == "HTTP 503"
    assert third.incidents == (incident,)
    assert repo.get_monitor(site.id).state is MonitorState.DOWN

    fourth = _run_nth(repo, probe, 3)

    assert (fourth.went_down, fourth.recovered, fourth.incidents) == (0, 0, ())
    assert len(repo.list_incidents(site.id)) == 1

    probe.down = set()
    back = _run_nth(repo, probe, 4)

    assert back.recovered == 1 and back.went_down == 0
    [closed] = repo.list_incidents(site.id)
    assert closed.id == incident.id
    assert closed.resolved_at == _check_time(4)
    assert closed.duration_seconds == 240
    assert back.incidents == (closed,)
    assert repo.get_monitor(site.id).state is MonitorState.UP


def test_two_failures_then_success_never_opens_an_incident():
    repo = MemoryUptimeRepo()
    site = _add(repo, "site")
    probe = RecordingProbe(down={"site"})

    summaries = [_run_nth(repo, probe, 0), _run_nth(repo, probe, 1)]
    probe.down = set()
    summaries.append(_run_nth(repo, probe, 2))

    assert all(s.went_down == 0 and s.recovered == 0 for s in summaries)
    assert repo.list_incidents(site.id) == []
    assert repo.get_monitor(site.id).state is MonitorState.UP


def test_custom_failure_threshold_goes_down_on_first_failure():
    repo = MemoryUptimeRepo()
    site = _add(repo, "site")

    summary = _run_nth(repo, RecordingProbe(down={"site"}), 0, failure_threshold=1)

    assert summary.went_down == 1
    assert summary.incidents[0].started_at == _check_time(0)
    assert repo.get_monitor(site.id).state is MonitorState.DOWN


def test_custom_recovery_threshold_needs_two_successes():
    repo = MemoryUptimeRepo()
    site = _add(repo, "site")
    probe = RecordingProbe(down={"site"})
    thresholds = {"failure_threshold": 1, "recovery_threshold": 2}
    _run_nth(repo, probe, 0, **thresholds)
    probe.down = set()

    first_success = _run_nth(repo, probe, 1, **thresholds)

    assert first_success.recovered == 0
    assert repo.get_monitor(site.id).state is MonitorState.DOWN
    assert repo.list_incidents(site.id)[0].is_open

    second_success = _run_nth(repo, probe, 2, **thresholds)

    assert second_success.recovered == 1
    assert second_success.incidents[0].duration_seconds == 120
    assert repo.get_monitor(site.id).state is MonitorState.UP
