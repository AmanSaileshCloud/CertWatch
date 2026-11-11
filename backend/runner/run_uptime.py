"""Uptime monitoring worker.

Runs continuously as the certwatch-uptime systemd service. Three modes:

  (no mode)        Loop forever: check every due monitor, wait out the rest of the
                   tick (UPTIME_TICK_SECONDS), repeat. Ctrl-C / SIGTERM stops it
                   promptly, cancelling an in-flight cycle.
  --once           Run one check cycle against the configured store and exit.
  --check URL ...  Ad-hoc check of one or more URLs. Nothing is saved — handy
                   for a quick demo:  python -m backend.runner.run_uptime --check https://example.com

Examples:
  python -m backend.runner.run_uptime
  python -m backend.runner.run_uptime --once
  python -m backend.runner.run_uptime --check https://example.com --keyword "Example Domain"
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import time
from collections.abc import Awaitable
from datetime import datetime, timezone

import httpx

from ..ssl_monitor.adapters.storage.base import UptimeRepoPort
from ..ssl_monitor.config.settings import Settings
from ..ssl_monitor.core.http_probe import make_http_client, probe_http
from ..ssl_monitor.core.uptime_models import CheckResult, Monitor
from ..ssl_monitor.handlers._deps import get_settings, make_uptime_repo
from ..ssl_monitor.services.uptime_checker import CycleSummary, UptimeProbeFn, run_cycle

logger = logging.getLogger("ssl_monitor.uptime.runner")

ADHOC_INTERVAL_SECONDS = 60


def _make_probe(client: httpx.AsyncClient, settings: Settings) -> UptimeProbeFn:
    """Bind the shared HTTP client and settings into a ``(monitor, now)`` probe."""

    async def probe(monitor: Monitor, now: datetime) -> CheckResult:
        return await probe_http(
            client,
            monitor,
            now=now,
            region=settings.uptime_region,
            max_body_bytes=settings.uptime_max_body_bytes,
        )

    return probe


def _open_client(settings: Settings) -> httpx.AsyncClient:
    return make_http_client(settings.uptime_user_agent, settings.uptime_max_concurrency)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _print_checks(results: list[tuple[str, CheckResult]]) -> None:
    print("-" * 90)
    print(f"{'URL':<40}{'STATE':<7}{'STATUS':>7}{'MS':>8}  ERROR")
    print("-" * 90)
    for url, r in results:
        state = "UP" if r.is_up else "DOWN"
        status = "-" if r.status_code is None else str(r.status_code)
        ms = "-" if r.response_ms is None else str(r.response_ms)
        print(f"{url:<40}{state:<7}{status:>7}{ms:>8}  {r.error or ''}")
    print("-" * 90)


def _print_summary(summary: CycleSummary) -> None:
    print(
        f"Checked {summary.checked} monitor(s) at {summary.checked_at.isoformat()}: "
        f"{summary.up} up, {summary.down} down"
    )


async def check_urls(
    urls: list[str], settings: Settings, *, timeout: int, keyword: str | None
) -> list[tuple[str, CheckResult]]:
    """Probe each URL once with a throwaway monitor. Nothing is saved."""
    now = _now()
    monitors = [
        Monitor(
            id=0,
            name=url,
            url=url,
            interval_seconds=ADHOC_INTERVAL_SECONDS,
            timeout_seconds=timeout,
            keyword=keyword,
        )
        for url in urls
    ]
    async with _open_client(settings) as client:
        probe = _make_probe(client, settings)
        results = await asyncio.gather(*(probe(m, now) for m in monitors))
    return list(zip(urls, results))


async def _cycle(settings: Settings, repo: UptimeRepoPort, probe: UptimeProbeFn) -> CycleSummary:
    return await run_cycle(
        repo,
        probe,
        now=_now(),
        max_concurrency=settings.uptime_max_concurrency,
        region=settings.uptime_region,
    )


async def run_once(settings: Settings, repo: UptimeRepoPort) -> CycleSummary:
    async with _open_client(settings) as client:
        return await _cycle(settings, repo, _make_probe(client, settings))


def _install_sigterm(stop: asyncio.Event) -> None:
    """On POSIX, let SIGTERM (systemd stop) end the loop cleanly."""
    if sys.platform != "win32":
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)


async def _run_until_stopped(cycle: Awaitable[CycleSummary], stop: asyncio.Event) -> None:
    """Run one cycle, cancelling it if a stop is requested meanwhile."""
    task = asyncio.ensure_future(cycle)
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    stopper.cancel()
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return
    try:
        task.result()
    except Exception:
        logger.exception("Uptime cycle failed (is the database up?)")


async def _sleep_until_next_tick(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        pass


async def run_forever(
    settings: Settings, repo: UptimeRepoPort, stop: asyncio.Event | None = None
) -> None:
    """Run a cycle every tick until stopped. A failing cycle is logged, not fatal."""
    stop = stop or asyncio.Event()
    _install_sigterm(stop)
    tick = settings.uptime_tick_seconds
    logger.info("Uptime worker started: tick %ss (Ctrl-C to stop)", tick)

    async with _open_client(settings) as client:
        probe = _make_probe(client, settings)
        while not stop.is_set():
            started = time.monotonic()
            await _run_until_stopped(_cycle(settings, repo, probe), stop)
            await _sleep_until_next_tick(stop, tick - (time.monotonic() - started))
    logger.info("Uptime worker stopped.")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uptime monitoring worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="append", metavar="URL", help="ad-hoc check (nothing saved); repeatable"
    )
    mode.add_argument("--once", action="store_true", help="run one check cycle and exit")
    parser.add_argument(
        "--timeout", type=int, default=10, help="--check timeout in seconds (default 10)"
    )
    parser.add_argument("--keyword", help="--check: text the page must contain")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    if not args.verbose:  # httpx logs every request at INFO — too noisy for a 24x7 loop
        logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()

    if args.check:
        results = asyncio.run(
            check_urls(args.check, settings, timeout=args.timeout, keyword=args.keyword)
        )
        _print_checks(results)
        return 0

    try:
        repo = make_uptime_repo(settings)
        if args.once:
            _print_summary(asyncio.run(run_once(settings, repo)))
        else:
            asyncio.run(run_forever(settings, repo))
    except KeyboardInterrupt:
        logger.info("Stopped.")
    except Exception as exc:
        logger.error("Uptime run failed (is the database up?): %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
