"""Uptime check cycle.

One cycle = claim the monitors that are due, check them concurrently (bounded),
and save every result in one go. The probe and clock are injected, so the cycle
is unit-testable with an in-memory repo and a fake probe — no network.

Depends only on the ``UptimeRepoPort`` and core models, never on httpx.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ..adapters.storage.base import UptimeRepoPort
from ..core.uptime_models import DEFAULT_REGION, CheckResult, Monitor

# (monitor, checked_at) -> CheckResult
UptimeProbeFn = Callable[[Monitor, datetime], Awaitable[CheckResult]]
Clock = Callable[[], datetime]

# Most monitors claimed per cycle; anything beyond waits for the next tick.
CLAIM_BATCH_LIMIT = 500

logger = logging.getLogger("ssl_monitor.uptime")


@dataclass(frozen=True)
class CycleSummary:
    checked_at: datetime
    checked: int
    up: int
    down: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _check_safely(
    probe: UptimeProbeFn, monitor: Monitor, checked_at: datetime, region: str
) -> CheckResult:
    """Run the probe; a bug in it becomes a "down" result instead of breaking the cycle."""
    try:
        return await probe(monitor, checked_at)
    except Exception as exc:
        logger.exception("Probe crashed for monitor %s", monitor.id)
        return CheckResult(
            monitor_id=monitor.id,
            checked_at=checked_at,
            is_up=False,
            error=f"Checker error: {type(exc).__name__}",
            region=region,
        )


async def run_cycle(
    repo: UptimeRepoPort,
    probe: UptimeProbeFn,
    *,
    now: datetime,
    max_concurrency: int,
    region: str = DEFAULT_REGION,
    clock: Clock = _utc_now,
) -> CycleSummary:
    """Check every due monitor once and persist the results.

    ``now`` decides which monitors are due; each result is stamped with
    ``clock()`` when its check actually starts (after waiting for a free slot).
    """
    monitors = repo.claim_due_monitors(now, CLAIM_BATCH_LIMIT)
    if not monitors:
        return CycleSummary(checked_at=now, checked=0, up=0, down=0)

    gate = asyncio.Semaphore(max_concurrency)

    async def bounded(monitor: Monitor) -> CheckResult:
        async with gate:
            return await _check_safely(probe, monitor, clock(), region)

    results = await asyncio.gather(*(bounded(m) for m in monitors))
    repo.save_check_results(results)

    up = sum(1 for r in results if r.is_up)
    summary = CycleSummary(checked_at=now, checked=len(results), up=up, down=len(results) - up)
    logger.info("Uptime cycle: checked=%d up=%d down=%d", summary.checked, summary.up, summary.down)
    return summary
