"""PostgreSQL storage adapter for SSL domains (implements :class:`StoragePort`)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert

from ...core.models import SOURCE_DIRECT, DomainRecord, Status
from ..db.tables import domains


def _record_to_row(record: DomainRecord) -> dict[str, Any]:
    row = asdict(record)
    row["status"] = record.status.value
    return row


def _row_to_record(row: Any) -> DomainRecord:
    data = dict(row._mapping)
    data["status"] = Status(data["status"])
    data["source"] = data["source"] or SOURCE_DIRECT
    return DomainRecord(**data)


class PostgresStorage:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def put(self, record: DomainRecord) -> None:
        row = _record_to_row(record)
        stmt = insert(domains).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[domains.c.domain],
            set_={k: stmt.excluded[k] for k in row if k != "domain"},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def insert_if_absent(self, record: DomainRecord) -> bool:
        """Insert unless the key exists. Returns True when a row was written."""
        stmt = (
            insert(domains)
            .values(**_record_to_row(record))
            .on_conflict_do_nothing()
            .returning(domains.c.domain)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).first() is not None

    def get(self, domain: str) -> DomainRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(domains).where(domains.c.domain == domain)).first()
        return _row_to_record(row) if row is not None else None

    def list(self) -> list[DomainRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(domains).order_by(domains.c.created_at)).all()
        return [_row_to_record(r) for r in rows]

    def delete(self, domain: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(domains).where(domains.c.domain == domain))
