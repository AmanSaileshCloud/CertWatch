"""Uptime runner CLI tests — no network, no database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.runner import run_uptime
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.http_probe import GuardedTransport
from backend.ssl_monitor.core.uptime_models import CheckResult
from backend.ssl_monitor.handlers import _deps


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
