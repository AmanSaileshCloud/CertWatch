"""In-memory uptime repository (implements :class:`UptimeRepoPort`).

For unit tests of the uptime service — same semantics as the Postgres adapter,
no database. Models are frozen dataclasses, so handing them out is safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ...core.uptime_models import CheckResult, Monitor


# Only configuration is taken from the caller, as in Postgres; runtime state starts fresh.
_CONFIG_FIELDS = (
    "name", "url", "interval_seconds", "timeout_seconds", "expected_status",
    "keyword", "customer_id", "group_id", "type", "is_paused",
)


def _next_check_at(monitor: Monitor, now: datetime) -> datetime:
    """Same rule as the Postgres adapter: stay on schedule, or restart from now."""
    interval = timedelta(seconds=monitor.interval_seconds)
    on_schedule = monitor.next_check_at + interval
    return on_schedule if on_schedule > now else now + interval


class MemoryUptimeRepo:
    def __init__(self) -> None:
        self._monitors: dict[int, Monitor] = {}
        self._results: list[CheckResult] = []
        self._next_id = 1

    def add_monitor(self, monitor: Monitor) -> Monitor:
        now = datetime.now(timezone.utc)
        stored = Monitor(
            id=self._next_id,
            next_check_at=monitor.next_check_at or now,
            created_at=now,
            updated_at=now,
            **{name: getattr(monitor, name) for name in _CONFIG_FIELDS},
        )
        self._monitors[stored.id] = stored
        self._next_id += 1
        return stored

    def get_monitor(self, monitor_id: int) -> Monitor | None:
        return self._monitors.get(monitor_id)

    def claim_due_monitors(self, now: datetime, limit: int) -> list[Monitor]:
        due = sorted(
            (m for m in self._monitors.values() if not m.is_paused and m.next_check_at <= now),
            key=lambda m: (m.next_check_at, m.id),
        )[:limit]
        for monitor in due:
            self._monitors[monitor.id] = replace(
                monitor, next_check_at=_next_check_at(monitor, now)
            )
        return due

    def save_check_results(self, results: Sequence[CheckResult]) -> None:
        for result in results:
            monitor = self._monitors.get(result.monitor_id)
            if monitor is None:
                continue
            self._results.append(result)
            self._monitors[monitor.id] = replace(
                monitor,
                last_checked_at=result.checked_at,
                last_status_code=result.status_code,
                last_response_ms=result.response_ms,
                last_error=result.error,
                updated_at=datetime.now(timezone.utc),
            )

    def list_check_results(self, monitor_id: int, limit: int = 100) -> list[CheckResult]:
        mine = [r for r in self._results if r.monitor_id == monitor_id]
        # Stable sort on reversed insertion order: ties fall back to newest-inserted first.
        return sorted(reversed(mine), key=lambda r: r.checked_at, reverse=True)[:limit]
