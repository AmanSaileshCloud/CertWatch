"""Data migrations, run down and up again against the test database."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import text

pytestmark = pytest.mark.postgres


def test_0005_marks_resolved_incidents_as_alerted_but_not_open_ones(pg, pg_alembic):
    command.downgrade(pg_alembic, "0004")
    try:
        with pg.begin() as conn:
            monitor_ids = conn.execute(text(
                "INSERT INTO monitors (name, url, interval_seconds) VALUES "
                "('A', 'https://a.test', 60), ('B', 'https://b.test', 60) RETURNING id"
            )).scalars().all()
            conn.execute(text(
                "INSERT INTO incidents (monitor_id, started_at, resolved_at, duration_seconds,"
                " close_reason) VALUES (:a, '2026-01-01T10:00Z', '2026-01-01T10:05Z', 300,"
                " 'recovered'), (:b, '2026-01-01T11:00Z', NULL, NULL, NULL)"
            ), {"a": monitor_ids[0], "b": monitor_ids[1]})
    finally:
        command.upgrade(pg_alembic, "head")

    with pg.connect() as conn:
        rows = conn.execute(text(
            "SELECT resolved_at, down_alert_sent_at, recovery_alert_sent_at FROM incidents"
            " ORDER BY started_at"
        )).all()
    resolved, still_open = rows
    assert resolved.down_alert_sent_at == resolved.recovery_alert_sent_at == resolved.resolved_at
    assert still_open.down_alert_sent_at is None and still_open.recovery_alert_sent_at is None
