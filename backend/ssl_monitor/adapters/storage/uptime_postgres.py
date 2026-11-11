"""PostgreSQL adapter for uptime monitors (implements :class:`UptimeRepoPort`)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Engine, bindparam, case, func, insert, literal, select, update

from ...core.uptime_models import CheckResult, Monitor, MonitorState, MonitorType
from ..db.tables import check_results, monitors

_CONFIG_FIELDS = (
    "customer_id",
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


def _last_fields_row(result: CheckResult) -> dict[str, Any]:
    return {
        "b_id": result.monitor_id,
        "b_checked_at": result.checked_at,
        "b_status_code": result.status_code,
        "b_response_ms": result.response_ms,
        "b_error": result.error,
    }


_UPDATE_LAST_FIELDS = (
    update(monitors)
    .where(monitors.c.id == bindparam("b_id"))
    .values(
        last_checked_at=bindparam("b_checked_at"),
        last_status_code=bindparam("b_status_code"),
        last_response_ms=bindparam("b_response_ms"),
        last_error=bindparam("b_error"),
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

    def save_check_results(self, results: Sequence[CheckResult]) -> None:
        if not results:
            return
        with self._engine.begin() as conn:
            live_ids = _lock_existing_monitor_ids(conn, {r.monitor_id for r in results})
            kept = [r for r in results if r.monitor_id in live_ids]
            if not kept:
                return
            conn.execute(insert(check_results), [_result_to_row(r) for r in kept])
            conn.execute(_UPDATE_LAST_FIELDS, [_last_fields_row(r) for r in kept])

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


def _lock_existing_monitor_ids(conn: Any, ids: set[int]) -> set[int]:
    """Return the ids that still exist, holding ``FOR KEY SHARE`` (the lightest
    lock that blocks DELETE) so none vanish before this transaction's inserts land."""
    stmt = (
        select(monitors.c.id)
        .where(monitors.c.id.in_(ids))
        .with_for_update(read=True, key_share=True)
    )
    return set(conn.execute(stmt).scalars())
