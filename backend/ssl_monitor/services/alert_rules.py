"""Alert rules — extra uptime-alert recipients per customer, per monitor, or global.

Validation lives here so the API error ``detail`` is one readable sentence. Only
the email channel is accepted (uptime alerts go by SES email only)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..adapters.storage.base import CatalogRepoPort, UptimeRepoPort
from ..core.alert_models import AlertRule
from ..core.uptime_models import AlertChannel
from .errors import AlertRuleNotFound, InvalidInput
from .monitors import get_monitor, require_customer

MAX_TARGET_LENGTH = 254
# local part: up to 64 characters, none of whitespace, control characters , ; < > quotes @;
# domain: dot-separated letter/digit/hyphen labels ending in a 2+ letter TLD.
_EMAIL_RE = re.compile(
    r"[^@\s\x00-\x1f\x7f,;<>\"'`]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}"
)
_FLAGS = ("on_down", "on_recovery", "enabled")


def is_valid_email(value: Any) -> bool:
    """A plain single address (``name@example.com``), safe to hand to SES as-is."""
    return (isinstance(value, str) and len(value) <= MAX_TARGET_LENGTH
            and _EMAIL_RE.fullmatch(value) is not None)


def _clean_target(value: Any) -> str:
    target = value.strip().lower() if isinstance(value, str) else ""
    if not is_valid_email(target):
        raise InvalidInput(f"invalid email address: {value!r}")
    return target


def _clean_channel(value: Any) -> AlertChannel:
    if value != AlertChannel.EMAIL.value:
        raise InvalidInput("channel must be 'email' (uptime alerts are sent by email only)")
    return AlertChannel.EMAIL


def _require_one_scope(customer_id: int | None, monitor_id: int | None) -> None:
    if customer_id is not None and monitor_id is not None:
        raise InvalidInput("give customer_id or monitor_id, not both")


def list_rules(
    catalog: CatalogRepoPort, *, customer_id: int | None, monitor_id: int | None
) -> list[AlertRule]:
    """Rules for exactly one scope; neither id means the global rules."""
    _require_one_scope(customer_id, monitor_id)
    return catalog.list_alert_rules(customer_id=customer_id, monitor_id=monitor_id)


def create_rule(
    catalog: CatalogRepoPort, repo: UptimeRepoPort, fields: Mapping[str, Any]
) -> AlertRule:
    """Raises InvalidInput, CustomerNotFound / MonitorNotFound, DuplicateNameError."""
    customer_id, monitor_id = fields.get("customer_id"), fields.get("monitor_id")
    _require_one_scope(customer_id, monitor_id)
    rule = AlertRule(
        id=0,
        target=_clean_target(fields.get("target")),
        channel=_clean_channel(fields.get("channel", AlertChannel.EMAIL.value)),
        customer_id=customer_id,
        monitor_id=monitor_id,
        **{flag: _clean_flag(flag, fields.get(flag, True)) for flag in _FLAGS},
    )
    if customer_id is not None:
        require_customer(catalog, customer_id)
    if monitor_id is not None:
        get_monitor(repo, monitor_id)
    return catalog.add_alert_rule(rule)


def is_global(customer_id: int | None, monitor_id: int | None) -> bool:
    """A rule with no customer and no monitor applies to every monitor's alerts."""
    return customer_id is None and monitor_id is None


def get_rule(catalog: CatalogRepoPort, rule_id: int) -> AlertRule:
    """Raises AlertRuleNotFound."""
    return update_rule(catalog, rule_id, {})


def update_rule(catalog: CatalogRepoPort, rule_id: int, fields: Mapping[str, Any]) -> AlertRule:
    unknown = set(fields) - set(_FLAGS)
    if unknown:
        raise InvalidInput(f"cannot change alert rule field(s): {', '.join(sorted(unknown))}")
    values = {flag: _clean_flag(flag, value) for flag, value in fields.items()}
    rule = catalog.update_alert_rule(rule_id, values)  # empty values: just look it up
    if rule is None:
        raise AlertRuleNotFound(f"alert rule {rule_id} not found")
    return rule


def delete_rule(catalog: CatalogRepoPort, rule_id: int) -> None:
    if not catalog.delete_alert_rule(rule_id):
        raise AlertRuleNotFound(f"alert rule {rule_id} not found")


def _clean_flag(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidInput(f"{name} must be true or false")
    return value
