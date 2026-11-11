"""Uptime runner CLI tests — no network, no database."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from backend.runner import run_uptime
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.http_probe import GuardedTransport
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor
from backend.ssl_monitor.handlers import _deps
from backend.ssl_monitor.services.uptime_alerts import AlertBackoff, AlertSummary
from backend.ssl_monitor.services.uptime_checker import CycleSummary


def test_check_mode_prints_up_and_down(monkeypatch, capsys):
    async def fake_probe_http(client, monitor, *, now, region, max_body_bytes):
        if "down" in monitor.url:
            return CheckResult(monitor.id, now, is_up=False, error="Connection refused")
        return CheckResult(monitor.id, now, is_up=True, status_code=200, response_ms=42)

    @asynccontextmanager
    async def fake_client():
        yield object()

    monkeypatch.setattr(run_uptime, "probe_http", fake_probe_http)
    monkeypatch.setattr(run_uptime, "_open_client", lambda settings: fake_client())
    monkeypatch.setattr(run_uptime, "get_settings", lambda: Settings.from_env({}))

    code = run_uptime.main(["--check", "https://up.test", "--check", "https://down.test"])

    out = capsys.readouterr().out
    assert code == 0
    up_line = next(line for line in out.splitlines() if "up.test" in line)
    down_line = next(line for line in out.splitlines() if "down.test" in line)
    assert "UP" in up_line and "200" in up_line and "42" in up_line
    assert "DOWN" in down_line and "Connection refused" in down_line


def test_once_and_check_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        run_uptime._parse_args(["--once", "--check", "https://x.test"])


def test_no_mode_means_loop():
    args = run_uptime._parse_args([])
    assert args.check is None and args.once is False


def test_make_uptime_repo_memory_is_a_singleton(monkeypatch):
    monkeypatch.setattr(_deps, "_MEMORY_UPTIME_REPO", None)
    settings = Settings.from_env({"STORAGE": "memory"})

    first = _deps.make_uptime_repo(settings)

    assert isinstance(first, MemoryUptimeRepo)
    assert _deps.make_uptime_repo(settings) is first


def _loop_settings() -> Settings:
    return Settings.from_env({"STORAGE": "memory", "UPTIME_TICK_SECONDS": "0.01"})


def _patch_cycle(monkeypatch, cycle):
    @asynccontextmanager
    async def fake_client():
        yield object()

    monkeypatch.setattr(run_uptime, "_open_client", lambda settings: fake_client())
    monkeypatch.setattr(run_uptime, "_cycle", cycle)


def test_loop_survives_a_failing_cycle_and_stops_on_request(monkeypatch):
    calls = []

    async def scenario():
        stop = asyncio.Event()

        async def cycle(settings, repo, probe):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database down")
            stop.set()

        _patch_cycle(monkeypatch, cycle)
        await asyncio.wait_for(
            run_uptime.run_forever(_loop_settings(), MemoryUptimeRepo(), stop), timeout=5)

    asyncio.run(scenario())
    assert len(calls) == 2


def test_heartbeat_is_recorded_only_after_a_successful_cycle(monkeypatch):
    calls, beats = [], []

    async def scenario():
        stop = asyncio.Event()

        async def cycle(settings, repo, probe):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database down")
            if len(calls) == 3:
                stop.set()

        _patch_cycle(monkeypatch, cycle)
        await asyncio.wait_for(run_uptime.run_forever(
            _loop_settings(), MemoryUptimeRepo(), stop, heartbeat=beats.append), timeout=5)

    asyncio.run(scenario())
    assert len(calls) == 3
    assert len(beats) == 2  # cycles 2 and 3; the failed first cycle sends no heartbeat


def test_a_failing_heartbeat_never_stops_the_worker(monkeypatch):
    calls = []

    def broken_heartbeat(at):
        raise ConnectionError("database down")

    async def scenario():
        stop = asyncio.Event()

        async def cycle(settings, repo, probe):
            calls.append(1)
            if len(calls) == 2:
                stop.set()

        _patch_cycle(monkeypatch, cycle)
        await asyncio.wait_for(run_uptime.run_forever(
            _loop_settings(), MemoryUptimeRepo(), stop, heartbeat=broken_heartbeat), timeout=5)

    asyncio.run(scenario())
    assert len(calls) == 2


def test_heartbeat_only_with_postgres_storage():
    assert run_uptime.make_heartbeat(_loop_settings()) is None


def test_stop_cancels_a_cycle_that_is_still_running(monkeypatch):
    cancelled = []

    async def scenario():
        stop = asyncio.Event()

        async def cycle(settings, repo, probe):
            try:
                stop.set()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        _patch_cycle(monkeypatch, cycle)
        await asyncio.wait_for(
            run_uptime.run_forever(_loop_settings(), MemoryUptimeRepo(), stop), timeout=5)

    asyncio.run(scenario())
    assert cancelled == [True]


@pytest.mark.parametrize("allow, guarded", [("false", True), ("true", False)])
def test_worker_client_honours_allow_private_targets(allow, guarded):
    settings = Settings.from_env(env={"UPTIME_ALLOW_PRIVATE_TARGETS": allow})

    async def transport():
        async with run_uptime._open_client(settings) as client:
            return client._transport

    assert isinstance(asyncio.run(transport()), GuardedTransport) is guarded


def test_loop_dispatches_alerts_and_survives_a_failing_dispatch(monkeypatch):
    dispatched = []

    async def scenario():
        stop = asyncio.Event()

        async def cycle(settings, repo, probe):
            return None

        def dispatch(*, now):
            dispatched.append(now)
            if len(dispatched) == 1:
                raise RuntimeError("SES down")
            stop.set()
            return AlertSummary(sent=1)

        _patch_cycle(monkeypatch, cycle)
        await asyncio.wait_for(run_uptime.run_forever(
            _loop_settings(), MemoryUptimeRepo(), stop, dispatch=dispatch), timeout=5)

    asyncio.run(scenario())
    assert len(dispatched) == 2


def test_once_prints_the_alert_summary(monkeypatch, capsys):
    summary = CycleSummary(checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                           checked=2, up=1, down=1)

    async def fake_run_once(settings, repo):
        return summary

    monkeypatch.setattr(_deps, "_MEMORY_UPTIME_REPO", None)  # restored after the test
    monkeypatch.setattr(run_uptime, "get_settings", lambda: _loop_settings())
    monkeypatch.setattr(run_uptime, "run_once", fake_run_once)
    monkeypatch.setattr(run_uptime, "make_alert_dispatch",
                        lambda settings, repo: lambda *, now: AlertSummary(sent=2, failed=1))

    assert run_uptime.main(["--once"]) == 0
    out = capsys.readouterr().out
    assert "Checked 2 monitor(s)" in out
    assert "Alerts: 2 sent, 1 failed" in out


def test_once_reports_a_failed_dispatch(monkeypatch, capsys):
    async def fake_run_once(settings, repo):
        return CycleSummary(checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                            checked=0, up=0, down=0)

    def broken(*, now):
        raise RuntimeError("db down")

    monkeypatch.setattr(_deps, "_MEMORY_UPTIME_REPO", None)  # restored after the test
    monkeypatch.setattr(run_uptime, "get_settings", lambda: _loop_settings())
    monkeypatch.setattr(run_uptime, "run_once", fake_run_once)
    monkeypatch.setattr(run_uptime, "make_alert_dispatch", lambda settings, repo: broken)

    assert run_uptime.main(["--once"]) == 0
    assert "Alerts: dispatch failed" in capsys.readouterr().out


def test_memory_repo_attaches_catalog_names_to_alert_candidates(monkeypatch):
    monkeypatch.setattr(_deps, "_MEMORY_UPTIME_REPO", None)
    monkeypatch.setattr(_deps, "_MEMORY_CATALOG_REPO", None)
    settings = Settings.from_env({"STORAGE": "memory"})
    catalog = _deps.make_catalog_repo(settings)
    customer = catalog.add_customer("Acme")
    group = catalog.add_group(customer.id, "Shops")
    repo = _deps.make_uptime_repo(settings)
    monitor = repo.add_monitor(Monitor(id=0, name="Shop", url="https://shop.test",
                                       interval_seconds=60, group_id=group.id,
                                       customer_id=customer.id))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo.record_checks([CheckResult(monitor.id, now, False)] * 3, evaluate)

    [candidate] = repo.alert_candidates(now)
    assert (candidate.customer_name, candidate.group_name) == ("Acme", "Shops")


def test_alert_dispatch_warns_once_without_default_recipients(monkeypatch, caplog):
    monkeypatch.setattr(_deps, "_MEMORY_CATALOG_REPO", None)
    settings = Settings.from_env({"STORAGE": "memory"})

    dispatch = run_uptime.make_alert_dispatch(settings, MemoryUptimeRepo())
    assert "No default alert recipients" not in caplog.text  # nothing read at start-up
    dispatch(now=datetime.now(timezone.utc))
    dispatch(now=datetime.now(timezone.utc))

    assert caplog.text.count("No default alert recipients") == 1


def test_worker_starts_while_the_settings_database_is_down(monkeypatch):
    """A boot-time DB outage must not stop the worker: the dispatch fails, logged."""
    monkeypatch.setattr(_deps, "_MEMORY_CATALOG_REPO", None)

    def db_down(settings):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(run_uptime, "make_alert_config", db_down)
    dispatch = run_uptime.make_alert_dispatch(Settings.from_env({"STORAGE": "memory"}),
                                              MemoryUptimeRepo())
    assert run_uptime.dispatch_safely(dispatch) is None


def test_slow_alert_sending_never_delays_the_checks(monkeypatch):
    """Cycles keep running while one dispatch is stuck; no second dispatch starts;
    stopping waits for the stuck one to finish."""
    cycles, dispatches = [], []
    release = threading.Event()

    async def scenario():
        stop = asyncio.Event()

        async def cycle(settings, repo, probe):
            cycles.append(1)
            if len(cycles) == 5:
                release.set()  # let the stuck dispatch finish, then stop
                stop.set()

        def dispatch(*, now):
            dispatches.append(now)
            release.wait(timeout=5)
            return AlertSummary()

        _patch_cycle(monkeypatch, cycle)
        await asyncio.wait_for(run_uptime.run_forever(
            _loop_settings(), MemoryUptimeRepo(), stop, dispatch=dispatch), timeout=5)

    asyncio.run(scenario())
    assert len(cycles) == 5
    assert len(dispatches) == 1


def test_alert_dispatch_rereads_settings_and_reuses_one_backoff(monkeypatch):
    monkeypatch.setattr(_deps, "_MEMORY_CATALOG_REPO", None)
    settings = Settings.from_env({"STORAGE": "memory"})
    configs, backoffs = [], []
    monkeypatch.setattr(run_uptime, "make_alert_config",
                        lambda s: configs.append(s) or _deps.make_alert_config(s))

    def fake_dispatch(*args, backoff, **kwargs):
        backoffs.append(backoff)
        return AlertSummary(sent=0, failed=0)

    monkeypatch.setattr(run_uptime, "dispatch_alerts", fake_dispatch)
    dispatch = run_uptime.make_alert_dispatch(settings, MemoryUptimeRepo())
    dispatch(now=datetime.now(timezone.utc))
    dispatch(now=datetime.now(timezone.utc))

    assert len(configs) == 2  # one fresh read per dispatch, none at start-up
    assert backoffs[0] is backoffs[1] and isinstance(backoffs[0], AlertBackoff)


def test_alert_dispatch_is_quiet_with_default_recipients(monkeypatch, caplog):
    monkeypatch.setattr(_deps, "_MEMORY_CATALOG_REPO", None)
    settings = Settings.from_env({"STORAGE": "memory", "ALERT_EMAIL": "ops@acme.test"})
    run_uptime.make_alert_dispatch(settings, MemoryUptimeRepo())
    assert "No default alert recipients" not in caplog.text
