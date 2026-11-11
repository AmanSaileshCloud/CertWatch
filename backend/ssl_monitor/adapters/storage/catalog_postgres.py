"""PostgreSQL adapter for customers, monitor groups and alert rules
(implements :class:`CatalogRepoPort`).

Cascades live in the schema: deleting a customer deletes its groups and alert rules
(ON DELETE CASCADE), deleting a monitor deletes its alert rules, and deleting a group
ungroups its domains and monitors (ON DELETE SET NULL).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import Connection, Engine, and_, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from ...core.alert_models import AlertKind, AlertRule
from ...core.catalog_models import Customer, MonitorGroup
from ...core.uptime_models import AlertChannel, Monitor
from ..db.tables import alert_rules, customers, monitor_groups
from .base import DuplicateNameError

_CUSTOMER_FIELDS = frozenset({"name", "contact_email"})
_ALERT_RULE_FIELDS = frozenset({"on_down", "on_recovery", "enabled"})


def _row_to_customer(row: Any) -> Customer:
    return Customer(**row._mapping)


def _row_to_group(row: Any) -> MonitorGroup:
    return MonitorGroup(**row._mapping)


def _row_to_rule(row: Any) -> AlertRule:
    data = dict(row._mapping)
    data["channel"] = AlertChannel(data["channel"])
    return AlertRule(**data)


def _same_scope(customer_id: int | None, monitor_id: int | None) -> Any:
    """Rules of exactly this scope (both None = global)."""
    return and_(alert_rules.c.customer_id.is_not_distinct_from(customer_id),
                alert_rules.c.monitor_id.is_not_distinct_from(monitor_id))


def _applies_to(monitor: Monitor) -> Any:
    """Global rules, the monitor's customer's rules and the monitor's own rules."""
    scopes = [_same_scope(None, None), alert_rules.c.monitor_id == monitor.id]
    if monitor.customer_id is not None:
        scopes.append(alert_rules.c.customer_id == monitor.customer_id)
    return or_(*scopes)


def _wants(kind: AlertKind) -> list[Any]:
    """The rule flag a ``kind`` alert needs (none for TEST)."""
    match kind:
        case AlertKind.DOWN | AlertKind.REMINDER:
            return [alert_rules.c.on_down]
        case AlertKind.RECOVERED:
            return [alert_rules.c.on_recovery]
    return []


def _require_one_scope(rule: AlertRule) -> None:
    if rule.customer_id is not None and rule.monitor_id is not None:
        raise ValueError("an alert rule is for a customer or a monitor, not both")


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

    def list_alert_rules(
        self, *, customer_id: int | None = None, monitor_id: int | None = None
    ) -> list[AlertRule]:
        stmt = (select(alert_rules).where(_same_scope(customer_id, monitor_id))
                .order_by(alert_rules.c.target, alert_rules.c.id))
        with self._engine.connect() as conn:
            return [_row_to_rule(row) for row in conn.execute(stmt)]

    def add_alert_rule(self, rule: AlertRule) -> AlertRule:
        _require_one_scope(rule)
        target = rule.target.strip().lower()
        stmt = insert(alert_rules).values(
            customer_id=rule.customer_id, monitor_id=rule.monitor_id,
            channel=rule.channel.value, target=target, on_down=rule.on_down,
            on_recovery=rule.on_recovery, enabled=rule.enabled,
        )
        # ux_alert_rules_scope_target rejects a duplicate, even from a concurrent insert.
        duplicate = f"{target} is already an alert recipient here"
        with self._write("customer or monitor", duplicate=duplicate) as conn:
            return _row_to_rule(conn.execute(stmt.returning(*alert_rules.c)).one())

    def update_alert_rule(self, rule_id: int, values: Mapping[str, Any]) -> AlertRule | None:
        unknown = set(values) - _ALERT_RULE_FIELDS
        if unknown:
            raise ValueError(f"cannot change alert rule field(s): {', '.join(sorted(unknown))}")
        if values:
            stmt = (update(alert_rules).where(alert_rules.c.id == rule_id).values(**values)
                    .returning(*alert_rules.c))
        else:
            stmt = select(alert_rules).where(alert_rules.c.id == rule_id)
        with self._engine.begin() as conn:
            row = conn.execute(stmt).first()
        return _row_to_rule(row) if row is not None else None

    def delete_alert_rule(self, rule_id: int) -> bool:
        return self._delete(alert_rules, rule_id)

    def recipients_for(self, monitor: Monitor, kind: AlertKind) -> list[str]:
        target = func.lower(alert_rules.c.target)
        stmt = (
            select(target).distinct()
            .where(alert_rules.c.enabled,
                   alert_rules.c.channel == AlertChannel.EMAIL.value,
                   _applies_to(monitor), *_wants(kind))
            .order_by(target)
        )
        with self._engine.connect() as conn:
            return list(conn.execute(stmt).scalars())

    def _delete(self, table: Any, row_id: int) -> bool:
        stmt = delete(table).where(table.c.id == row_id).returning(table.c.id)
        with self._engine.begin() as conn:
            return conn.execute(stmt).first() is not None

    @contextmanager
    def _write(
        self, referenced: str = "customer", *, duplicate: str = "that name is already taken"
    ) -> Iterator[Connection]:
        """A transaction that turns a unique violation into DuplicateNameError(``duplicate``)
        and a missing ``referenced`` row into ValueError (the memory repo's exceptions)."""
        try:
            with self._engine.begin() as conn:
                yield conn
        except IntegrityError as exc:
            if isinstance(exc.orig, UniqueViolation):
                raise DuplicateNameError(duplicate) from exc
            if isinstance(exc.orig, ForeignKeyViolation):
                raise ValueError(f"{referenced} does not exist") from exc
            raise
