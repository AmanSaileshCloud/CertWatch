"""In-memory customers, monitor groups and alert rules (implements :class:`CatalogRepoPort`).

For unit tests and dev — same semantics as the Postgres adapter, no database.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from ...core.alert_models import AlertKind, AlertRule
from ...core.catalog_models import Customer, MonitorGroup
from ...core.uptime_models import AlertChannel, Monitor
from .base import DuplicateNameError

_CUSTOMER_FIELDS = frozenset({"name", "contact_email"})
_ALERT_RULE_FIELDS = frozenset({"on_down", "on_recovery", "enabled"})

GroupsDeletedHook = Callable[[set[int]], None]
CustomerDeletedHook = Callable[[int], None]


class MemoryCatalogRepo:
    """Customers and groups held in dicts.

    In Postgres, deleting a group ungroups its SSL domains and website monitors, and
    deleting a customer also clears their ``customer_id``, via the foreign keys. This
    repo does not own those records, so it reports deletions instead:
    ``on_groups_deleted`` is called with the ids of every group removed —
    ``{group_id}`` for ``delete_group``, the customer's group ids for
    ``delete_customer`` — and ``on_customer_deleted`` with a deleted customer's id;
    the wiring uses them to clear ``group_id`` / ``customer_id`` on the in-memory
    domains and monitors. A deleted customer's alert rules go with it; monitors are not
    known here, so a monitor's rules are not checked against or removed with it."""

    def __init__(
        self,
        on_groups_deleted: GroupsDeletedHook | None = None,
        on_customer_deleted: CustomerDeletedHook | None = None,
    ) -> None:
        self._on_groups_deleted = on_groups_deleted
        self._on_customer_deleted = on_customer_deleted
        self._customers: dict[int, Customer] = {}
        self._groups: dict[int, MonitorGroup] = {}
        self._next_customer_id = 1
        self._next_group_id = 1
        self._rules: dict[int, AlertRule] = {}
        self._next_rule_id = 1

    def list_customers(self) -> list[Customer]:
        return sorted(self._customers.values(), key=lambda c: (c.name, c.id))

    def get_customer(self, customer_id: int) -> Customer | None:
        return self._customers.get(customer_id)

    def add_customer(self, name: str, contact_email: str | None = None) -> Customer:
        self._require_free_customer_name(name)
        customer = Customer(id=self._next_customer_id, name=name, contact_email=contact_email,
                            created_at=_now())
        self._customers[customer.id] = customer
        self._next_customer_id += 1
        return customer

    def update_customer(self, customer_id: int, values: Mapping[str, Any]) -> Customer | None:
        unknown = set(values) - _CUSTOMER_FIELDS
        if unknown:
            raise ValueError(f"cannot change customer field(s): {', '.join(sorted(unknown))}")
        customer = self._customers.get(customer_id)
        if customer is None:
            return None
        if "name" in values:
            self._require_free_customer_name(values["name"], ignore_id=customer_id)
        self._customers[customer_id] = replace(customer, **values)
        return self._customers[customer_id]

    def delete_customer(self, customer_id: int) -> bool:
        if self._customers.pop(customer_id, None) is None:
            return False
        self._rules = {r.id: r for r in self._rules.values() if r.customer_id != customer_id}
        self._remove_groups({g.id for g in self._groups.values()
                             if g.customer_id == customer_id})
        if self._on_customer_deleted is not None:
            self._on_customer_deleted(customer_id)
        return True

    def list_groups(self, customer_id: int | None = None) -> list[MonitorGroup]:
        matching = [g for g in self._groups.values()
                    if customer_id is None or g.customer_id == customer_id]
        return sorted(matching, key=lambda g: (g.name, g.id))

    def get_group(self, group_id: int) -> MonitorGroup | None:
        return self._groups.get(group_id)

    def add_group(self, customer_id: int, name: str) -> MonitorGroup:
        if customer_id not in self._customers:
            raise ValueError(f"customer {customer_id} does not exist")
        self._require_free_group_name(customer_id, name)
        group = MonitorGroup(id=self._next_group_id, customer_id=customer_id, name=name,
                             created_at=_now())
        self._groups[group.id] = group
        self._next_group_id += 1
        return group

    def rename_group(self, group_id: int, name: str) -> MonitorGroup | None:
        group = self._groups.get(group_id)
        if group is None:
            return None
        self._require_free_group_name(group.customer_id, name, ignore_id=group_id)
        self._groups[group_id] = replace(group, name=name)
        return self._groups[group_id]

    def delete_group(self, group_id: int) -> bool:
        if group_id not in self._groups:
            return False
        self._remove_groups({group_id})
        return True

    def list_alert_rules(
        self, *, customer_id: int | None = None, monitor_id: int | None = None
    ) -> list[AlertRule]:
        matching = [r for r in self._rules.values()
                    if (r.customer_id, r.monitor_id) == (customer_id, monitor_id)]
        return sorted(matching, key=lambda r: (r.target, r.id))

    def add_alert_rule(self, rule: AlertRule) -> AlertRule:
        if rule.customer_id is not None and rule.monitor_id is not None:
            raise ValueError("an alert rule is for a customer or a monitor, not both")
        if rule.customer_id is not None and rule.customer_id not in self._customers:
            raise ValueError(f"customer {rule.customer_id} does not exist")
        target = rule.target.strip().lower()
        if any(r.target == target for r in self.list_alert_rules(
                customer_id=rule.customer_id, monitor_id=rule.monitor_id)):
            raise DuplicateNameError(f"{target} is already an alert recipient here")
        stored = replace(rule, id=self._next_rule_id, target=target, created_at=_now())
        self._rules[stored.id] = stored
        self._next_rule_id += 1
        return stored

    def update_alert_rule(self, rule_id: int, values: Mapping[str, Any]) -> AlertRule | None:
        unknown = set(values) - _ALERT_RULE_FIELDS
        if unknown:
            raise ValueError(f"cannot change alert rule field(s): {', '.join(sorted(unknown))}")
        rule = self._rules.get(rule_id)
        if rule is None:
            return None
        self._rules[rule_id] = replace(rule, **values)
        return self._rules[rule_id]

    def delete_alert_rule(self, rule_id: int) -> bool:
        return self._rules.pop(rule_id, None) is not None

    def recipients_for(self, monitor: Monitor, kind: AlertKind) -> list[str]:
        return sorted({r.target.lower() for r in self._rules.values()
                       if r.enabled and r.channel is AlertChannel.EMAIL
                       and _applies_to(r, monitor) and _wants(r, kind)})

    def _remove_groups(self, group_ids: set[int]) -> None:
        for group_id in group_ids:
            del self._groups[group_id]
        if group_ids and self._on_groups_deleted is not None:
            self._on_groups_deleted(group_ids)

    def _require_free_customer_name(self, name: str, ignore_id: int | None = None) -> None:
        if any(c.name == name and c.id != ignore_id for c in self._customers.values()):
            raise DuplicateNameError(f"customer {name!r} already exists")

    def _require_free_group_name(
        self, customer_id: int, name: str, ignore_id: int | None = None
    ) -> None:
        if any(g.customer_id == customer_id and g.name == name and g.id != ignore_id
               for g in self._groups.values()):
            raise DuplicateNameError(f"group {name!r} already exists for this customer")


def _applies_to(rule: AlertRule, monitor: Monitor) -> bool:
    """Global rules, the monitor's customer's rules and the monitor's own rules."""
    if rule.monitor_id is not None:
        return rule.monitor_id == monitor.id
    return rule.customer_id is None or rule.customer_id == monitor.customer_id


def _wants(rule: AlertRule, kind: AlertKind) -> bool:
    match kind:
        case AlertKind.DOWN | AlertKind.REMINDER:
            return rule.on_down
        case AlertKind.RECOVERED:
            return rule.on_recovery
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)
