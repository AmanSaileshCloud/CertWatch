"""The extra numbers shown in an uptime alert email: the site's resolved IP, an
availability summary (last 3 days) and a performance summary (last 24 hours).

Best effort: the alert must go out even if a lookup here fails, so ``build_report``
never raises — a failed part is simply left out of the email.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from ..adapters.storage.base import UptimeRepoPort
from ..core.alert_models import AlertReport
from ..core.uptime_models import CheckResult, Incident, Monitor

logger = logging.getLogger("ssl_monitor.uptime.alerts")

SUMMARY_DAYS = 3
PERFORMANCE_HOURS = 24
MAX_INCIDENTS = 200
MAX_RESULTS = 1500  # 24 h of 1-minute checks fits
DNS_TIMEOUT_SECONDS = 2.0

IpResolver = Callable[[str], str | None]

# One small pool for DNS lookups, so a hung lookup can be abandoned after the timeout
# instead of holding up the alert (socket.getaddrinfo itself has no timeout).
_dns_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="alert-dns")


def resolve_host_ip(url: str) -> str | None:
    """The first IP address the URL's host resolves to, or None (no host, lookup
    failed, or slower than DNS_TIMEOUT_SECONDS)."""
    host = urlsplit(url).hostname
    if not host:
        return None
    try:
        infos = _dns_pool.submit(socket.getaddrinfo, host, None).result(DNS_TIMEOUT_SECONDS)
    except (OSError, FutureTimeout):
        return None
    return str(infos[0][4][0]) if infos else None


def build_report(
    repo: UptimeRepoPort,
    monitor: Monitor,
    now: datetime,
    resolve_ip: IpResolver | None = None,
) -> AlertReport | None:
    """The report for ``monitor`` at ``now``; None if the history could not be read."""
    try:
        summary_start = now - timedelta(days=SUMMARY_DAYS)
        perf_start = now - timedelta(hours=PERFORMANCE_HOURS)
        counts = repo.uptime_counts(summary_start, monitor_ids=[monitor.id]).get(monitor.id)
        incidents = repo.list_incidents(monitor.id, limit=MAX_INCIDENTS)
        results = repo.list_check_results(monitor.id, limit=MAX_RESULTS, since=perf_start)
    except Exception:  # noqa: BLE001 - the alert still goes out without the report
        logger.exception("Could not build the alert report for monitor %s", monitor.id)
        return None
    downtimes, downtime_seconds = _downtime(incidents, summary_start, now)
    return AlertReport(
        resolved_ip=resolve_ip(monitor.url) if resolve_ip else None,
        summary_days=SUMMARY_DAYS,
        availability_pct=_percent(counts),
        downtimes=downtimes,
        downtime_seconds=downtime_seconds,
        performance_hours=PERFORMANCE_HOURS,
        response_series=tuple(results),
        **_performance(results),
    )


def _percent(counts: tuple[int, int] | None) -> float | None:
    """(total, up) → up as a percentage; None when there were no checks."""
    if not counts or counts[0] == 0:
        return None
    total, up = counts
    return up * 100.0 / total


def _downtime(incidents: Sequence[Incident], start: datetime, end: datetime) -> tuple[int, float]:
    """How many incidents overlap [start, end] and their total downtime inside it."""
    count, seconds = 0, 0.0
    for incident in incidents:
        began = max(_utc(incident.started_at), start)
        ended = min(_utc(incident.resolved_at) if incident.resolved_at else end, end)
        if ended > began or (incident.is_open and began <= end):
            count += 1
            seconds += max(0.0, (ended - began).total_seconds())
    return count, seconds


def _performance(results: Sequence[CheckResult]) -> dict[str, object]:
    times = [r.response_ms for r in results if r.is_up and r.response_ms is not None]
    up = sum(1 for r in results if r.is_up)
    return {
        "checks": len(results),
        "uptime_pct": _percent((len(results), up)),
        "avg_response_ms": round(sum(times) / len(times)) if times else None,
        "min_response_ms": min(times) if times else None,
        "max_response_ms": max(times) if times else None,
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
