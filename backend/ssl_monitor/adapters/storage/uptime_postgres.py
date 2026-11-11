"""PostgreSQL adapter for uptime monitors (implements :class:`UptimeRepoPort`)."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    DateTime,
    Engine,
    Integer,
    any_,
    bindparam,
    case,
    cast,
    delete,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...core.alert_models import AlertCandidate, AlertKind
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
from ...core.incident_rules import evaluate_in_order
from ..db.tables import check_results, incidents, monitors
from .base import IncidentRules
from .uptime_alerts_postgres import (
    INCIDENT_PREFIX,
    MONITOR_PREFIX,
    alert_candidates_query,
    alert_dispatch_lock,
    mark_alert_sent_statement,
    unprefixed,
)

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
    "alerts_enabled",
    "reminder_minutes",
    "alert_delay_minutes",
)

# Fields a user change (``change_monitor``) may set; runtime state is not among them.
_EDITABLE_FIELDS = frozenset(_CONFIG_FIELDS) | {"next_check_at", "alerts_enabled_at"}

_RESET_STATE = {
    "state": MonitorState.UNKNOWN.value,
    "consecutive_failures": 0,
    "consecutive_successes": 0,
    "first_failure_at": None,
}

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
    return _monitor_from(dict(row._mapping))


def _monitor_from(data: dict[str, Any]) -> Monitor:
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
    return _incident_from(dict(row._mapping))


def _incident_from(data: dict[str, Any]) -> Incident:
    if data["close_reason"] is not None:
        data["close_reason"] = CloseReason(data["close_reason"])
    return Incident(**data)


def _row_to_candidate(row: Any) -> AlertCandidate:
    return AlertCandidate(
        incident=_incident_from(unprefixed(row, INCIDENT_PREFIX)),
        monitor=_monitor_from(unprefixed(row, MONITOR_PREFIX)),
        customer_name=row.customer_name,
        group_name=row.group_name,
    )


def _change_to_row(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ``change_monitor`` mapping and convert it to column values."""
    unknown = set(values) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"cannot change monitor field(s): {', '.join(sorted(unknown))}")
    row = dict(values)
    if "type" in row:
        row["type"] = MonitorType(row["type"]).value
    return row


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

    def list_check_results(
        self, monitor_id: int, limit: int = 100, since: datetime | None = None
    ) -> list[CheckResult]:
        stmt = (
            select(*_RESULT_COLUMNS)
            .where(check_results.c.monitor_id == monitor_id)
            .order_by(check_results.c.checked_at.desc(), check_results.c.id.desc())
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(check_results.c.checked_at >= since)
        with self._engine.connect() as conn:
            return [_row_to_result(row) for row in conn.execute(stmt)]

    def list_monitors(self) -> list[Monitor]:
        stmt = select(monitors).order_by(monitors.c.name, monitors.c.id)
        with self._engine.connect() as conn:
            return [_row_to_monitor(row) for row in conn.execute(stmt)]

    def change_monitor(
        self,
        monitor_id: int,
        values: Mapping[str, Any],
        *,
        now: datetime,
        reset_state: bool = False,
        close_reason: CloseReason | None = None,
    ) -> Monitor | None:
        row = _change_to_row(values) | (_RESET_STATE if reset_state else {}) | {"updated_at": now}
        lock = select(monitors.c.id).where(monitors.c.id == monitor_id).with_for_update(
            key_share=True)
        change = update(monitors).where(monitors.c.id == monitor_id).values(**row)
        with self._engine.begin() as conn:
            if conn.execute(lock).first() is None:
                return None
            if close_reason is not None:
                _close_incident(conn, monitor_id, now, close_reason)
            return _row_to_monitor(conn.execute(change.returning(*monitors.c)).one())

    def delete_monitor(self, monitor_id: int) -> bool:
        stmt = delete(monitors).where(monitors.c.id == monitor_id).returning(monitors.c.id)
        with self._engine.begin() as conn:
            return conn.execute(stmt).first() is not None

    def uptime_counts(
        self, since: datetime, monitor_ids: Collection[int] | None = None
    ) -> dict[int, tuple[int, int]]:
        stmt = (
            select(
                check_results.c.monitor_id,
                func.count(),
                func.count().filter(check_results.c.is_up),
            )
            .where(check_results.c.checked_at >= since)
            .group_by(check_results.c.monitor_id)
        )
        if monitor_ids is not None:
            ids = bindparam("monitor_ids", list(monitor_ids), type_=ARRAY(Integer))
            stmt = stmt.where(check_results.c.monitor_id == any_(ids))
        with self._engine.connect() as conn:
            return {monitor_id: (total, up) for monitor_id, total, up in conn.execute(stmt)}

    def alert_candidates(self, now: datetime) -> list[AlertCandidate]:
        with self._engine.connect() as conn:
            return [_row_to_candidate(row) for row in conn.execute(alert_candidates_query(now))]

    def mark_alert_sent(self, incident_id: int, kind: AlertKind, at: datetime) -> None:
        stmt = mark_alert_sent_statement(incident_id, kind, at)
        if stmt is None:
            return
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def alert_dispatch_lock(self) -> AbstractContextManager[bool]:
        return alert_dispatch_lock(self._engine)


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
            result = transition.result
            changed.append(_close_incident(conn, result.monitor_id, result.checked_at,
                                           CloseReason.RECOVERED))
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


def _close_incident(
    conn: Any, monitor_id: int, at: datetime, reason: CloseReason
) -> Incident | None:
    """Resolve the monitor's open incident at ``at``; None if none is open."""
    resolved_at = literal(at, DateTime(timezone=True))
    elapsed = func.floor(func.extract("epoch", resolved_at - incidents.c.started_at))
    stmt = (
        update(incidents)
        .where(incidents.c.monitor_id == monitor_id, incidents.c.resolved_at.is_(None))
        .values(resolved_at=resolved_at,
                duration_seconds=cast(func.greatest(0, elapsed), Integer),
                close_reason=reason.value)
        .returning(*incidents.c)
    )
    row = conn.execute(stmt).first()
    return _row_to_incident(row) if row is not None else None
