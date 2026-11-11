"""In-memory uptime repository (implements :class:`UptimeRepoPort`).

For unit tests of the uptime service — same semantics as the Postgres adapter,
no database. Models are frozen dataclasses, so handing them out is safe.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from ...core.incident_rules import apply_transition, evaluate_in_order
from ...core.uptime_models import (
    CheckResult,
    CloseReason,
    Incident,
    IncidentEvent,
    Monitor,
    MonitorState,
    MonitorTransition,
    MonitorType,
)
from .base import IncidentRules


# Only configuration is taken from the caller, as in Postgres; runtime state starts fresh.
_CONFIG_FIELDS = (
    "name", "url", "interval_seconds", "timeout_seconds", "expected_status",
    "keyword", "customer_id", "group_id", "type", "is_paused",
)
_EDITABLE_FIELDS = frozenset(_CONFIG_FIELDS) | {"next_check_at"}

_RESET_STATE = {
    "state": MonitorState.UNKNOWN,
    "consecutive_failures": 0,
    "consecutive_successes": 0,
    "first_failure_at": None,
}


def _validated_change(values: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(values) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"cannot change monitor field(s): {', '.join(sorted(unknown))}")
    change = dict(values)
    if "type" in change:
        change["type"] = MonitorType(change["type"])
    return change


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
                result = transition.result
                changed.append(self._close_incident(result.monitor_id, result.checked_at,
                                                    CloseReason.RECOVERED))
        return [incident for incident in changed if incident is not None]

    def list_incidents(self, monitor_id: int | None = None, limit: int = 100) -> list[Incident]:
        matching = [i for i in self._incidents.values()
                    if monitor_id is None or i.monitor_id == monitor_id]
        return sorted(matching, key=lambda i: (i.started_at, i.id), reverse=True)[:limit]

    def list_check_results(
        self, monitor_id: int, limit: int = 100, since: datetime | None = None
    ) -> list[CheckResult]:
        mine = [r for r in self._results if r.monitor_id == monitor_id
                and (since is None or r.checked_at >= since)]
        # Stable sort on reversed insertion order: ties fall back to newest-inserted first.
        return sorted(reversed(mine), key=lambda r: r.checked_at, reverse=True)[:limit]

    def list_monitors(self) -> list[Monitor]:
        return sorted(self._monitors.values(), key=lambda m: (m.name, m.id))

    def change_monitor(
        self,
        monitor_id: int,
        values: Mapping[str, Any],
        *,
        now: datetime,
        reset_state: bool = False,
        close_reason: CloseReason | None = None,
    ) -> Monitor | None:
        change = _validated_change(values) | (_RESET_STATE if reset_state else {})
        monitor = self._monitors.get(monitor_id)
        if monitor is None:
            return None
        if close_reason is not None:
            self._close_incident(monitor_id, now, close_reason)
        self._monitors[monitor_id] = replace(monitor, **change, updated_at=now)
        return self._monitors[monitor_id]

    def delete_monitor(self, monitor_id: int) -> bool:
        if self._monitors.pop(monitor_id, None) is None:
            return False
        self._results = [r for r in self._results if r.monitor_id != monitor_id]
        self._incidents = {i.id: i for i in self._incidents.values()
                           if i.monitor_id != monitor_id}
        return True

    def uptime_counts(
        self, since: datetime, monitor_ids: Collection[int] | None = None
    ) -> dict[int, tuple[int, int]]:
        wanted = None if monitor_ids is None else set(monitor_ids)
        counts: dict[int, tuple[int, int]] = {}
        for result in self._results:
            if result.checked_at >= since and (wanted is None or result.monitor_id in wanted):
                total, up = counts.get(result.monitor_id, (0, 0))
                counts[result.monitor_id] = (total + 1, up + result.is_up)
        return counts

    def _save_transition(self, transition: MonitorTransition) -> None:
        result = transition.result
        self._results.append(replace(result, monitor_version=None))  # not stored, as in SQL
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

    def _close_incident(
        self, monitor_id: int, at: datetime, reason: CloseReason
    ) -> Incident | None:
        incident = self._find_open_incident(monitor_id)
        if incident is None:
            return None
        elapsed = (at - incident.started_at).total_seconds()
        closed = replace(incident, resolved_at=at, duration_seconds=max(0, int(elapsed)),
                         close_reason=reason)
        self._incidents[closed.id] = closed
        return closed
