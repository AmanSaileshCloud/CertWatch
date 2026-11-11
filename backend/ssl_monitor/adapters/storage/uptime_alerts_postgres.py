"""Alert queries for :class:`PostgresUptimeRepo` — candidates, sent marks, dispatch lock.

Statements and row splitting only; turning rows into models stays in uptime_postgres.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, Select, Update, and_, func, or_, select, update

from ...core.alert_models import AlertKind, RECENTLY_CLOSED_WINDOW_HOURS
from ..db.tables import customers, incidents, monitor_groups, monitors

logger = logging.getLogger("ssl_monitor.uptime.alerts")

# Key of the session-level advisory lock held while one worker dispatches alerts
# (any constant unique within this database; "CW" + 1).
ALERT_DISPATCH_LOCK_KEY = 0x4357_0001

# Incident and monitor columns are selected side by side; prefixes keep their
# identically named columns (id, ...) apart.
INCIDENT_PREFIX, MONITOR_PREFIX = "i_", "m_"
_CANDIDATE_COLUMNS = (
    *(column.label(INCIDENT_PREFIX + column.name) for column in incidents.c),
    *(column.label(MONITOR_PREFIX + column.name) for column in monitors.c),
    customers.c.name.label("customer_name"),
    monitor_groups.c.name.label("group_name"),
)


def alert_candidates_query(now: datetime) -> Select:
    """Open incidents, plus recently closed ones whose recovery alert is unsent, for
    monitors with alerts enabled; oldest first."""
    recently = now - timedelta(hours=RECENTLY_CLOSED_WINDOW_HOURS)
    needs_recovery_alert = and_(incidents.c.resolved_at >= recently,
                                incidents.c.recovery_alert_sent_at.is_(None))
    return (
        select(*_CANDIDATE_COLUMNS)
        .select_from(
            incidents.join(monitors, monitors.c.id == incidents.c.monitor_id)
            .outerjoin(customers, customers.c.id == monitors.c.customer_id)
            .outerjoin(monitor_groups, monitor_groups.c.id == monitors.c.group_id)
        )
        .where(monitors.c.alerts_enabled,
               or_(incidents.c.resolved_at.is_(None), needs_recovery_alert))
        .order_by(incidents.c.started_at, incidents.c.id)
    )


def unprefixed(row: Any, prefix: str) -> dict[str, Any]:
    """The columns of one side of a candidate row, without their prefix."""
    return {key.removeprefix(prefix): value for key, value in row._mapping.items()
            if key.startswith(prefix)}


def mark_alert_sent_statement(incident_id: int, kind: AlertKind, at: datetime) -> Update | None:
    """The UPDATE recording a sent alert of ``kind``; None for TEST (never recorded)."""
    values = _alert_sent_values(kind, at)
    if values is None:
        return None
    return update(incidents).where(incidents.c.id == incident_id).values(**values)


def _alert_sent_values(kind: AlertKind, at: datetime) -> dict[str, Any] | None:
    match kind:
        case AlertKind.DOWN:
            return {"down_alert_sent_at": at}
        case AlertKind.REMINDER:
            return {"last_reminder_sent_at": at,
                    "reminder_count": incidents.c.reminder_count + 1}
        case AlertKind.RECOVERED:
            return {"recovery_alert_sent_at": at}
    return None


@contextmanager
def alert_dispatch_lock(engine: Engine) -> Iterator[bool]:
    """Try the dispatch advisory lock on a dedicated connection held for the block;
    yields whether it was acquired, and always releases it afterwards."""
    with engine.connect() as conn:
        acquired = conn.execute(select(func.pg_try_advisory_lock(ALERT_DISPATCH_LOCK_KEY))
                                ).scalar_one()
        conn.commit()  # a session-level lock outlives the transaction; don't idle in one
        try:
            yield acquired
        finally:
            if acquired:
                _unlock(conn)


def _unlock(conn: Any) -> None:
    try:
        conn.execute(select(func.pg_advisory_unlock(ALERT_DISPATCH_LOCK_KEY)))
        conn.commit()
    except Exception:
        # The lock dies with the session; make sure a broken connection is not reused.
        logger.exception("Could not release the alert dispatch lock; dropping the connection")
        conn.invalidate()
