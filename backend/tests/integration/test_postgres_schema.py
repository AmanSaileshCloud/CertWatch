"""The migrated PostgreSQL schema: matches the code, and enforces its own rules."""

from __future__ import annotations

import datetime as dt

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import insert, inspect
from sqlalchemy.exc import IntegrityError

from backend.ssl_monitor.adapters.db.tables import incidents, metadata, monitors

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _add_monitor(conn, **overrides) -> int:
    values = {"name": "Example", "url": "https://example.com", "interval_seconds": 60}
    values.update(overrides)
    return conn.execute(insert(monitors).values(**values).returning(monitors.c.id)).scalar_one()


def test_migration_matches_table_definitions(pg):
    with pg.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


def test_check_results_has_monitor_time_index(pg):
    indexes = inspect(pg).get_indexes("check_results")
    assert any(ix["column_names"] == ["monitor_id", "checked_at"] for ix in indexes)


def test_rejects_interval_outside_1_5_10_minutes(pg):
    with pytest.raises(IntegrityError), pg.begin() as conn:
        _add_monitor(conn, interval_seconds=120)


def test_only_one_open_incident_per_monitor(pg):
    with pg.begin() as conn:
        monitor_id = _add_monitor(conn)
        conn.execute(insert(incidents).values(monitor_id=monitor_id, started_at=NOW))
    with pytest.raises(IntegrityError), pg.begin() as conn:
        conn.execute(insert(incidents).values(monitor_id=monitor_id, started_at=NOW))


def test_closed_incidents_do_not_block_a_new_one(pg):
    with pg.begin() as conn:
        monitor_id = _add_monitor(conn)
        conn.execute(insert(incidents).values(
            monitor_id=monitor_id, started_at=NOW, resolved_at=NOW, duration_seconds=0))
        conn.execute(insert(incidents).values(monitor_id=monitor_id, started_at=NOW))
