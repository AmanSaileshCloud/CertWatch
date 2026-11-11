"""PostgreSQL adapter for uptime monitors (implements :class:`UptimeRepoPort`)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Engine,
    Integer,
    bindparam,
    case,
    cast,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...core.uptime_models import (
    CheckResult,
    Incident,
    IncidentEvent,
    Monitor,
    MonitorState,
    MonitorTransition,
    MonitorType,
)
from ...core.incident_rules import evaluate_in_order
from .base import IncidentRules
from ..db.tables import check_results, incidents, monitors

_CONFIG_FIELDS = (
    "customer_id",
    "group_id",
    "name",
    "type",
    "url",
    "interval_seconds",
    "timeout_seconds",
    "expected_status",
    "keyword",
    "is_paused",
)

_RESULT_COLUMNS = (
    check_results.c.monitor_id,
    check_results.c.checked_at,
    check_results.c.is_up,
    check_results.c.status_code,
    check_results.c.response_ms,
    check_results.c.error,
    check_results.c.region,
)


def _row_to_monitor(row: Any) -> Monitor:
    data = dict(row._mapping)
    data["type"] = MonitorType(data["type"])
    data["state"] = MonitorState(data["state"])
    return Monitor(**data)


def _row_to_result(row: Any) -> CheckResult:
    return CheckResult(**row._mapping)


def _monitor_to_row(monitor: Monitor) -> dict[str, Any]:
    row = {name: getattr(monitor, name) for name in _CONFIG_FIELDS}
    row["type"] = monitor.type.value
    if monitor.next_check_at is not None:
        row["next_check_at"] = monitor.next_check_at
    return row


def _result_to_row(result: CheckResult) -> dict[str, Any]:
    return {column.name: getattr(result, column.name) for column in _RESULT_COLUMNS}


def _row_to_incident(row: Any) -> Incident:
    return Incident(**row._mapping)


def _monitor_state_row(transition: MonitorTransition) -> dict[str, Any]:
    result = transition.result
    return {
        "b_id": result.monitor_id,
        "b_checked_at": result.checked_at,
        "b_status_code": result.status_code,
        "b_response_ms": result.response_ms,
        "b_error": result.error,
        "b_state": transition.state.value,
        "b_failures": transition.consecutive_failures,
        "b_successes": transition.consecutive_successes,
        "b_first_failure_at": transition.first_failure_at,
    }


_UPDATE_MONITOR_STATE = (
    update(monitors)
    .where(monitors.c.id == bindparam("b_id"))
    .values(
        last_checked_at=bindparam("b_checked_at"),
        last_status_code=bindparam("b_status_code"),
        last_response_ms=bindparam("b_response_ms"),
        last_error=bindparam("b_error"),
        state=bindparam("b_state"),
        consecutive_failures=bindparam("b_failures"),
        consecutive_successes=bindparam("b_successes"),
        first_failure_at=bindparam("b_first_failure_at"),
        updated_at=func.now(),
    )
)


class PostgresUptimeRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_monitor(self, monitor: Monitor) -> Monitor:
        stmt = insert(monitors).values(**_monitor_to_row(monitor)).returning(*monitors.c)
        with self._engine.begin() as conn:
            return _row_to_monitor(conn.execute(stmt).one())

    def get_monitor(self, monitor_id: int) -> Monitor | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(monitors).where(monitors.c.id == monitor_id)).first()
        return _row_to_monitor(row) if row is not None else None

    def claim_due_monitors(self, now: datetime, limit: int) -> list[Monitor]:
        due = (
            select(monitors)
            .where(~monitors.c.is_paused, monitors.c.next_check_at <= now)
            .order_by(monitors.c.next_check_at, monitors.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        with self._engine.begin() as conn:
            claimed = [_row_to_monitor(row) for row in conn.execute(due)]
            if claimed:
                conn.execute(_advance_next_check(now, [m.id for m in claimed]))
        return claimed

    def record_checks(self, results: Sequence[CheckResult], rules: IncidentRules) -> list[Incident]:
        if not results:
            return []
        with self._engine.begin() as conn:
            current = _lock_active_monitors(conn, {r.monitor_id for r in results})
            transitions = evaluate_in_order(current, results, rules)
            if not transitions:
                return []
            conn.execute(insert(check_results), [_result_to_row(t.result) for t in transitions])
            conn.execute(_UPDATE_MONITOR_STATE, [_monitor_state_row(t) for t in transitions])
            return _apply_incident_events(conn, transitions)

    def list_incidents(self, monitor_id: int | None = None, limit: int = 100) -> list[Incident]:
        stmt = (
            select(incidents)
            .order_by(incidents.c.started_at.desc(), incidents.c.id.desc())
            .limit(limit)
        )
        if monitor_id is not None:
            stmt = stmt.where(incidents.c.monitor_id == monitor_id)
        with self._engine.connect() as conn:
            return [_row_to_incident(row) for row in conn.execute(stmt)]

    def list_check_results(self, monitor_id: int, limit: int = 100) -> list[CheckResult]:
        stmt = (
            select(*_RESULT_COLUMNS)
            .where(check_results.c.monitor_id == monitor_id)
            .order_by(check_results.c.checked_at.desc(), check_results.c.id.desc())
            .limit(limit)
        )
        with self._engine.connect() as conn:
            return [_row_to_result(row) for row in conn.execute(stmt)]


def _advance_next_check(now: datetime, ids: list[int]):
    """Keep the cadence anchored to the due time (no drift); if the worker fell a
    whole interval behind, restart from ``now`` rather than firing back-to-back."""
    interval = func.make_interval(0, 0, 0, 0, 0, 0, monitors.c.interval_seconds)
    on_schedule = monitors.c.next_check_at + interval
    from_now = literal(now, DateTime(timezone=True)) + interval
    return (
        update(monitors)
        .where(monitors.c.id.in_(ids))
        .values(next_check_at=case((on_schedule > now, on_schedule), else_=from_now))
    )


def _lock_active_monitors(conn: Any, ids: set[int]) -> dict[int, Monitor]:
    """Lock the unpaused monitors among ``ids`` (FOR NO KEY UPDATE: blocks concurrent
    edits/pauses/deletes until commit) and return their CURRENT stored state."""
    stmt = (
        select(monitors)
        .where(monitors.c.id.in_(ids), ~monitors.c.is_paused)
        .with_for_update(key_share=True)
    )
    return {row.id: _row_to_monitor(row) for row in conn.execute(stmt)}


def _apply_incident_events(conn: Any, transitions: list[MonitorTransition]) -> list[Incident]:
    """Open/close incidents in transition order; return only rows actually changed."""
    changed = []
    for transition in transitions:
        if transition.event is IncidentEvent.WENT_DOWN:
            changed.append(_open_incident(conn, transition))
        elif transition.event is IncidentEvent.RECOVERED:
            changed.append(_close_incident(conn, transition.result))
    return [incident for incident in changed if incident is not None]


def _open_incident(conn: Any, transition: MonitorTransition) -> Incident | None:
    """Insert an open incident unless the monitor already has one (partial unique index)."""
    result = transition.result
    stmt = (
        pg_insert(incidents)
        .values(
            monitor_id=result.monitor_id,
            started_at=transition.first_failure_at or result.checked_at,
            reason=result.error,
        )
        .on_conflict_do_nothing(
            index_elements=["monitor_id"], index_where=incidents.c.resolved_at.is_(None)
        )
        .returning(*incidents.c)
    )
    row = conn.execute(stmt).first()
    return _row_to_incident(row) if row is not None else None


def _close_incident(conn: Any, result: CheckResult) -> Incident | None:
    """Resolve the monitor's open incident at ``result.checked_at``; None if none is open."""
    resolved_at = literal(result.checked_at, DateTime(timezone=True))
    elapsed = func.floor(func.extract("epoch", resolved_at - incidents.c.started_at))
    stmt = (
        update(incidents)
        .where(incidents.c.monitor_id == result.monitor_id, incidents.c.resolved_at.is_(None))
        .values(resolved_at=resolved_at,
                duration_seconds=cast(func.greatest(0, elapsed), Integer))
        .returning(*incidents.c)
    )
    row = conn.execute(stmt).first()
    return _row_to_incident(row) if row is not None else None
