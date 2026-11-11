"""Worker heartbeats + a database ping, for /health/ready (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.tables import worker_heartbeats


class PostgresHeartbeats:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def beat(self, name: str, at: datetime) -> None:
        stmt = pg_insert(worker_heartbeats).values(name=name, beat_at=at)
        stmt = stmt.on_conflict_do_update(index_elements=["name"], set_={"beat_at": at})
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def last_beat(self, name: str) -> datetime | None:
        with self._engine.connect() as conn:
            return conn.execute(
                select(worker_heartbeats.c.beat_at).where(worker_heartbeats.c.name == name)
            ).scalar()

    def ping(self) -> None:
        """Raise if the database can't be reached."""
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))
