"""In-memory uptime repository (implements :class:`UptimeRepoPort`).

For unit tests of the uptime service — same semantics as the Postgres adapter,
no database. Models are frozen dataclasses, so handing them out is safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ...core.incident_rules import apply_transition, evaluate_in_order
from ...core.uptime_models import (
    CheckResult,
    Incident,
    IncidentEvent,
    Monitor,
    MonitorTransition,
)
from .base import IncidentRules


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
        self._incidents: dict[int, Incident] = {}
        self._next_id = 1
        self._next_incident_id = 1

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

    def record_checks(self, results: Sequence[CheckResult], rules: IncidentRules) -> list[Incident]:
        active = {m.id: m for m in self._monitors.values() if not m.is_paused}
        changed = []
        for transition in evaluate_in_order(active, results, rules):
            self._save_transition(transition)
            if transition.event is IncidentEvent.WENT_DOWN:
                changed.append(self._open_incident(transition))
            elif transition.event is IncidentEvent.RECOVERED:
                changed.append(self._close_incident(transition.result))
        return [incident for incident in changed if incident is not None]

    def list_incidents(self, monitor_id: int | None = None, limit: int = 100) -> list[Incident]:
        matching = [i for i in self._incidents.values()
                    if monitor_id is None or i.monitor_id == monitor_id]
        return sorted(matching, key=lambda i: (i.started_at, i.id), reverse=True)[:limit]

    def list_check_results(self, monitor_id: int, limit: int = 100) -> list[CheckResult]:
        mine = [r for r in self._results if r.monitor_id == monitor_id]
        # Stable sort on reversed insertion order: ties fall back to newest-inserted first.
        return sorted(reversed(mine), key=lambda r: r.checked_at, reverse=True)[:limit]

    def _save_transition(self, transition: MonitorTransition) -> None:
        result = transition.result
        self._results.append(result)
        self._monitors[result.monitor_id] = replace(
            apply_transition(self._monitors[result.monitor_id], transition),
            updated_at=datetime.now(timezone.utc),
        )

    def _find_open_incident(self, monitor_id: int) -> Incident | None:
        return next((i for i in self._incidents.values()
                     if i.monitor_id == monitor_id and i.is_open), None)

    def _open_incident(self, transition: MonitorTransition) -> Incident | None:
        if self._find_open_incident(transition.monitor_id) is not None:
            return None
        result = transition.result
        incident = Incident(
            id=self._next_incident_id,
            monitor_id=result.monitor_id,
            started_at=transition.first_failure_at or result.checked_at,
            reason=result.error,
        )
        self._incidents[incident.id] = incident
        self._next_incident_id += 1
        return incident

    def _close_incident(self, result: CheckResult) -> Incident | None:
        incident = self._find_open_incident(result.monitor_id)
        if incident is None:
            return None
        elapsed = (result.checked_at - incident.started_at).total_seconds()
        closed = replace(incident, resolved_at=result.checked_at,
                         duration_seconds=max(0, int(elapsed)))
        self._incidents[closed.id] = closed
        return closed
