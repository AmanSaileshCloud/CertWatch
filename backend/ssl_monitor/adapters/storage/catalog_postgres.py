"""PostgreSQL adapter for customers and monitor groups (implements :class:`CatalogRepoPort`).

Cascades live in the schema: deleting a customer deletes its groups (ON DELETE
CASCADE), and deleting a group ungroups its domains and monitors (ON DELETE SET NULL).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import Connection, Engine, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from ...core.catalog_models import Customer, MonitorGroup
from ..db.tables import customers, monitor_groups
from .base import DuplicateNameError

_CUSTOMER_FIELDS = frozenset({"name", "contact_email"})


def _row_to_customer(row: Any) -> Customer:
    return Customer(**row._mapping)


def _row_to_group(row: Any) -> MonitorGroup:
    return MonitorGroup(**row._mapping)


class PostgresCatalogRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_customers(self) -> list[Customer]:
        stmt = select(customers).order_by(customers.c.name, customers.c.id)
        with self._engine.connect() as conn:
            return [_row_to_customer(row) for row in conn.execute(stmt)]

    def get_customer(self, customer_id: int) -> Customer | None:
        stmt = select(customers).where(customers.c.id == customer_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return _row_to_customer(row) if row is not None else None

    def add_customer(self, name: str, contact_email: str | None = None) -> Customer:
        stmt = insert(customers).values(name=name, contact_email=contact_email)
        with self._write() as conn:
            return _row_to_customer(conn.execute(stmt.returning(*customers.c)).one())

    def update_customer(self, customer_id: int, values: Mapping[str, Any]) -> Customer | None:
        unknown = set(values) - _CUSTOMER_FIELDS
        if unknown:
            raise ValueError(f"cannot change customer field(s): {', '.join(sorted(unknown))}")
        if not values:
            return self.get_customer(customer_id)
        stmt = update(customers).where(customers.c.id == customer_id).values(**values)
        with self._write() as conn:
            row = conn.execute(stmt.returning(*customers.c)).first()
        return _row_to_customer(row) if row is not None else None

    def delete_customer(self, customer_id: int) -> bool:
        return self._delete(customers, customer_id)

    def list_groups(self, customer_id: int | None = None) -> list[MonitorGroup]:
        stmt = select(monitor_groups).order_by(monitor_groups.c.name, monitor_groups.c.id)
        if customer_id is not None:
            stmt = stmt.where(monitor_groups.c.customer_id == customer_id)
        with self._engine.connect() as conn:
            return [_row_to_group(row) for row in conn.execute(stmt)]

    def get_group(self, group_id: int) -> MonitorGroup | None:
        stmt = select(monitor_groups).where(monitor_groups.c.id == group_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return _row_to_group(row) if row is not None else None

    def add_group(self, customer_id: int, name: str) -> MonitorGroup:
        stmt = insert(monitor_groups).values(customer_id=customer_id, name=name)
        with self._write() as conn:
            return _row_to_group(conn.execute(stmt.returning(*monitor_groups.c)).one())

    def rename_group(self, group_id: int, name: str) -> MonitorGroup | None:
        stmt = update(monitor_groups).where(monitor_groups.c.id == group_id).values(name=name)
        with self._write() as conn:
            row = conn.execute(stmt.returning(*monitor_groups.c)).first()
        return _row_to_group(row) if row is not None else None

    def delete_group(self, group_id: int) -> bool:
        return self._delete(monitor_groups, group_id)

    def _delete(self, table: Any, row_id: int) -> bool:
        stmt = delete(table).where(table.c.id == row_id).returning(table.c.id)
        with self._engine.begin() as conn:
            return conn.execute(stmt).first() is not None

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        """A transaction that turns a unique-name violation into DuplicateNameError and a
        missing customer into ValueError (the memory repo's exceptions)."""
        try:
            with self._engine.begin() as conn:
                yield conn
        except IntegrityError as exc:
            if isinstance(exc.orig, UniqueViolation):
                raise DuplicateNameError("that name is already taken") from exc
            if isinstance(exc.orig, ForeignKeyViolation):
                raise ValueError("customer does not exist") from exc
            raise
