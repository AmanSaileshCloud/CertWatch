"""SQLite storage adapter — the single-server persistence backend."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from ...core.models import SOURCE_DIRECT, DomainRecord, Status

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain               TEXT PRIMARY KEY,
    host                 TEXT NOT NULL,
    port                 INTEGER NOT NULL,
    status               TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    not_after            TEXT,
    days_remaining       INTEGER,
    last_checked_at      TEXT,
    last_error           TEXT,
    last_alert_threshold INTEGER,
    alerts_enabled       INTEGER NOT NULL DEFAULT 1,
    source               TEXT NOT NULL DEFAULT 'direct',
    agent_id             TEXT,
    stale_alert_sent     INTEGER NOT NULL DEFAULT 0
);
"""

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` is a no-op
# on an existing database, so a deployed certwatch.db keeps its old shape unless
# we ALTER it — and a missing `source` column would make every agent-managed row
# read back as direct, resurrecting the overwrite bug on upgrade.
_ADDED_COLUMNS = {
    "source": "TEXT NOT NULL DEFAULT 'direct'",
    "agent_id": "TEXT",
    "stale_alert_sent": "INTEGER NOT NULL DEFAULT 0",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_record(row: sqlite3.Row) -> DomainRecord:
    return DomainRecord(
        domain=row["domain"],
        host=row["host"],
        port=int(row["port"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        not_after=_parse_dt(row["not_after"]),
        days_remaining=(int(row["days_remaining"]) if row["days_remaining"] is not None else None),
        status=Status(row["status"]),
        last_checked_at=_parse_dt(row["last_checked_at"]),
        last_error=row["last_error"],
        last_alert_threshold=(
            int(row["last_alert_threshold"]) if row["last_alert_threshold"] is not None else None
        ),
        alerts_enabled=bool(row["alerts_enabled"]),
        source=row["source"] or SOURCE_DIRECT,
        agent_id=row["agent_id"],
        stale_alert_sent=bool(row["stale_alert_sent"]),
    )


def _record_to_params(record: DomainRecord) -> dict[str, Any]:
    return {
        "domain": record.domain,
        "host": record.host,
        "port": record.port,
        "status": record.status.value,
        "created_at": record.created_at.isoformat(),
        "not_after": _iso(record.not_after),
        "days_remaining": record.days_remaining,
        "last_checked_at": _iso(record.last_checked_at),
        "last_error": record.last_error,
        "last_alert_threshold": record.last_alert_threshold,
        "alerts_enabled": 1 if record.alerts_enabled else 0,
        "source": record.source,
        "agent_id": record.agent_id,
        "stale_alert_sent": 1 if record.stale_alert_sent else 0,
    }


class SqliteStorage:
    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema. Idempotent."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(domains)")}
        for column, ddl in _ADDED_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE domains ADD COLUMN {column} {ddl}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def put(self, record: DomainRecord) -> None:
        params = _record_to_params(record)
        columns = ", ".join(params)
        placeholders = ", ".join(f":{k}" for k in params)
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO domains ({columns}) VALUES ({placeholders})",
                params,
            )

    def get(self, domain: str) -> DomainRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM domains WHERE domain = ?", (domain,)
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list(self) -> list[DomainRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM domains ORDER BY created_at").fetchall()
        return [_row_to_record(r) for r in rows]

    def delete(self, domain: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM domains WHERE domain = ?", (domain,))
