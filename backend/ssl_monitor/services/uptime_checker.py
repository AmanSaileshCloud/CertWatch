"""Uptime check cycle.

One cycle = claim the monitors that are due, check them concurrently (bounded),
run each result through the incident rules, and save everything in one go. The
probe and clock are injected, so the cycle is unit-testable with an in-memory
repo and a fake probe — no network.

Depends only on the ``UptimeRepoPort`` and core models, never on httpx.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from datetime import datetime, timezone

from ..adapters.storage.base import UptimeRepoPort
from ..core.incident_rules import DEFAULT_FAILURE_THRESHOLD, DEFAULT_RECOVERY_THRESHOLD, evaluate
from ..core.uptime_models import DEFAULT_REGION, CheckResult, Incident, Monitor

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
    went_down: int = 0
    recovered: int = 0
    incidents: tuple[Incident, ...] = ()  # opened or closed this cycle


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _check_safely(
    probe: UptimeProbeFn, monitor: Monitor, checked_at: datetime, region: str
) -> CheckResult:
    """Run the probe; a bug in it becomes a "down" result instead of breaking the cycle.

    The result is stamped with the claimed monitor's ``updated_at``, so the save step
    can drop it if the monitor is edited while the check is in flight."""
    try:
        result = await probe(monitor, checked_at)
    except Exception as exc:
        logger.exception("Probe crashed for monitor %s", monitor.id)
        result = CheckResult(
            monitor_id=monitor.id,
            checked_at=checked_at,
            is_up=False,
            error=f"Checker error: {type(exc).__name__}",
            region=region,
        )
    return replace(result, monitor_version=monitor.updated_at)


def _log_incident_changes(incidents: Sequence[Incident]) -> None:
    for incident in incidents:
        if incident.is_open:
            logger.warning(
                "Monitor %s DOWN since %s - %s",
                incident.monitor_id,
                incident.started_at.isoformat(),
                incident.reason or "unknown reason",
            )
        else:
            logger.warning(
                "Monitor %s RECOVERED after %ss", incident.monitor_id, incident.duration_seconds
            )


async def run_cycle(
    repo: UptimeRepoPort,
    probe: UptimeProbeFn,
    *,
    now: datetime,
    max_concurrency: int,
    region: str = DEFAULT_REGION,
    clock: Clock = _utc_now,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    recovery_threshold: int = DEFAULT_RECOVERY_THRESHOLD,
) -> CycleSummary:
    """Check every due monitor once, apply the incident rules, and persist the outcome.

    The rules run inside the repo's save transaction against each monitor's current
    stored state, so a pause or edit made while a check was in flight wins (an edited
    monitor's in-flight result is dropped).

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
    rules = partial(
        evaluate, failure_threshold=failure_threshold, recovery_threshold=recovery_threshold
    )
    incidents = repo.record_checks(results, rules)
    _log_incident_changes(incidents)

    up = sum(1 for r in results if r.is_up)
    went_down = sum(1 for i in incidents if i.is_open)
    summary = CycleSummary(
        checked_at=now,
        checked=len(results),
        up=up,
        down=len(results) - up,
        went_down=went_down,
        recovered=len(incidents) - went_down,
        incidents=tuple(incidents),
    )
    logger.info(
        "Uptime cycle: checked=%d up=%d down=%d went_down=%d recovered=%d",
        summary.checked,
        summary.up,
        summary.down,
        summary.went_down,
        summary.recovered,
    )
    return summary
