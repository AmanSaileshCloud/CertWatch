"""Website-monitor service — plain functions over the uptime and catalog ports.

Validation, the customer/group ownership rules, and the state rules for edits
(which changes reset the monitor's up/down state) live here, so the API and any
future Lambda handler share them. No web framework here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..adapters.storage.base import CatalogRepoPort, UptimeRepoPort
from ..core.catalog_models import Customer, MonitorGroup
from ..core.uptime_models import (
    ALLOWED_INTERVAL_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    CheckResult,
    CloseReason,
    Incident,
    Monitor,
    MonitorState,
)
from .errors import CustomerNotFound, GroupNotFound, InvalidMonitor, MonitorNotFound

# url -> normalised url; raises InvalidMonitor / BlockedTarget (see url_policy).
UrlCheck = Callable[[str], str]

MAX_NAME_LENGTH = 200
MAX_KEYWORD_LENGTH = 200
EDITABLE_FIELDS = (
    "name", "url", "interval_seconds", "timeout_seconds", "expected_status",
    "keyword", "group_id", "customer_id",
)
# Changing what "up" means invalidates the current state and closes any open incident.
_TARGET_FIELDS = frozenset({"url", "expected_status", "keyword", "timeout_seconds"})
_OWNER_FIELDS = ("group_id", "customer_id")
UPTIME_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class MonitorView:
    """A monitor plus the derived values the dashboard shows."""

    monitor: Monitor
    status: str
    uptime_24h: float | None
    down_since: datetime | None
    customer_name: str | None
    group_name: str | None


# ── Validation ───────────────────────────────────────────────────


def _clean_name(value: Any) -> str:
    name = (value or "").strip() if isinstance(value, str) else ""
    if not name:
        raise InvalidMonitor("name must not be blank")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidMonitor(f"name must be at most {MAX_NAME_LENGTH} characters")
    return name


def _clean_interval(value: Any) -> int:
    if value not in ALLOWED_INTERVAL_SECONDS:
        allowed = ", ".join(str(v) for v in ALLOWED_INTERVAL_SECONDS)
        raise InvalidMonitor(f"interval_seconds must be one of {allowed}")
    return value


def _clean_timeout(value: Any) -> int:
    if not isinstance(value, int) or not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise InvalidMonitor(
            f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
        )
    return value


def _clean_expected_status(value: Any) -> int:
    if not isinstance(value, int) or not 100 <= value <= 599:
        raise InvalidMonitor("expected_status must be between 100 and 599")
    return value


def _clean_keyword(value: Any) -> str | None:
    """Trimmed keyword; blank means "no keyword"."""
    value = value.strip() if isinstance(value, str) else value
    if not value:
        return None
    if len(value) > MAX_KEYWORD_LENGTH:
        raise InvalidMonitor(f"keyword must be at most {MAX_KEYWORD_LENGTH} characters")
    return value


def _same(value: Any) -> Any:
    return value


_CLEANERS: dict[str, Callable[[Any], Any]] = {
    "name": _clean_name,
    "interval_seconds": _clean_interval,
    "timeout_seconds": _clean_timeout,
    "expected_status": _clean_expected_status,
    "keyword": _clean_keyword,
    "group_id": _same,
    "customer_id": _same,
}


def _clean(fields: Mapping[str, Any], check_url: UrlCheck) -> dict[str, Any]:
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise InvalidMonitor(f"unknown field(s): {', '.join(sorted(unknown))}")
    cleaned = {k: _CLEANERS[k](v) for k, v in fields.items() if k != "url"}
    if "url" in fields:
        cleaned["url"] = check_url(fields["url"])
    return cleaned


# ── Ownership (customer / group) ─────────────────────────────────


def require_group(catalog: CatalogRepoPort, group_id: int) -> MonitorGroup:
    group = catalog.get_group(group_id)
    if group is None:
        raise GroupNotFound(f"group {group_id} not found")
    return group


def require_customer(catalog: CatalogRepoPort, customer_id: int) -> Customer:
    customer = catalog.get_customer(customer_id)
    if customer is None:
        raise CustomerNotFound(f"customer {customer_id} not found")
    return customer


def _resolve_owner(
    catalog: CatalogRepoPort, group_id: int | None, customer_id: int | None, customer_given: bool
) -> tuple[int | None, int | None]:
    """A group decides the customer; a non-null different customer is an error
    (a null customer_id next to a group just means "take the group's")."""
    if group_id is not None:
        group = require_group(catalog, group_id)
        if customer_given and customer_id != group.customer_id:
            raise InvalidMonitor("customer_id does not match the group's customer")
        return group_id, group.customer_id
    if customer_id is not None:
        require_customer(catalog, customer_id)
    return None, customer_id


# ── Reads ────────────────────────────────────────────────────────


def get_monitor(repo: UptimeRepoPort, monitor_id: int) -> Monitor:
    monitor = repo.get_monitor(monitor_id)
    if monitor is None:
        raise MonitorNotFound(f"monitor {monitor_id} not found")
    return monitor


def list_monitors(
    repo: UptimeRepoPort, *, group_id: int | None = None, customer_id: int | None = None
) -> list[Monitor]:
    return [
        m for m in repo.list_monitors()
        if (group_id is None or m.group_id == group_id)
        and (customer_id is None or m.customer_id == customer_id)
    ]


def monitor_status(monitor: Monitor) -> str:
    """'paused' wins over the confirmed state."""
    return "paused" if monitor.is_paused else monitor.state.value


def describe_monitors(
    repo: UptimeRepoPort, catalog: CatalogRepoPort, monitors: list[Monitor], now: datetime
) -> list[MonitorView]:
    counts = repo.uptime_counts(
        now - UPTIME_WINDOW, monitor_ids=[m.id for m in monitors]
    ) if monitors else {}
    customers = {c.id: c.name for c in catalog.list_customers()}
    groups = {g.id: g.name for g in catalog.list_groups()}
    return [
        MonitorView(
            monitor=m,
            status=monitor_status(m),
            uptime_24h=_uptime_percent(counts.get(m.id)),
            down_since=m.first_failure_at if monitor_status(m) == "down" else None,
            customer_name=customers.get(m.customer_id),
            group_name=groups.get(m.group_id),
        )
        for m in monitors
    ]


def _uptime_percent(count: tuple[int, int] | None) -> float | None:
    if not count or count[0] == 0:
        return None
    total, up = count
    return round(up / total * 100, 2)


def list_checks(repo: UptimeRepoPort, monitor_id: int, limit: int) -> list[CheckResult]:
    get_monitor(repo, monitor_id)
    return repo.list_check_results(monitor_id, limit=limit)


def check_series(
    repo: UptimeRepoPort, monitor_id: int, hours: int, now: datetime
) -> list[CheckResult]:
    """Results in the last ``hours``, oldest first (for the response-time chart)."""
    get_monitor(repo, monitor_id)
    since = now - timedelta(hours=hours)
    max_results = hours * 3600 // min(ALLOWED_INTERVAL_SECONDS) + 1
    results = repo.list_check_results(monitor_id, limit=max_results, since=since)
    return list(reversed(results))


def list_incidents(repo: UptimeRepoPort, monitor_id: int, limit: int) -> list[Incident]:
    get_monitor(repo, monitor_id)
    return repo.list_incidents(monitor_id, limit=limit)


# ── Writes ───────────────────────────────────────────────────────


def prepare_monitor(
    catalog: CatalogRepoPort, fields: Mapping[str, Any], *, check_url: UrlCheck, now: datetime
) -> Monitor:
    """Validate a new monitor without saving it (absent and null fields mean "default")."""
    given = {k: v for k, v in fields.items() if v is not None}
    for required in ("name", "url"):
        if required not in given:
            raise InvalidMonitor(f"{required} is required")
    values = _clean(given, check_url)
    group_id, customer_id = _resolve_owner(
        catalog, values.pop("group_id", None), values.pop("customer_id", None),
        customer_given="customer_id" in given,
    )
    return Monitor(id=0, group_id=group_id, customer_id=customer_id, next_check_at=now, **values)


def create_monitor(
    repo: UptimeRepoPort,
    catalog: CatalogRepoPort,
    fields: Mapping[str, Any],
    *,
    check_url: UrlCheck,
    now: datetime,
) -> Monitor:
    """Validate and add a monitor; it is checked straight away."""
    return repo.add_monitor(prepare_monitor(catalog, fields, check_url=check_url, now=now))


def update_monitor(
    repo: UptimeRepoPort,
    catalog: CatalogRepoPort,
    monitor_id: int,
    fields: Mapping[str, Any],
    *,
    check_url: UrlCheck,
    now: datetime,
) -> Monitor:
    """Apply a partial edit. Target changes reset state and close the open incident
    ("edited"); an interval change only reschedules; name/owner changes do neither."""
    current = get_monitor(repo, monitor_id)
    values = _clean(fields, check_url)
    if any(f in fields for f in _OWNER_FIELDS):
        values["group_id"], values["customer_id"] = _resolve_owner(
            catalog,
            values.get("group_id", current.group_id),
            values.get("customer_id", current.customer_id),
            customer_given=fields.get("customer_id") is not None,
        )
    changed = {k: v for k, v in values.items() if getattr(current, k) != v}
    retarget = bool(_TARGET_FIELDS & changed.keys())
    if retarget or "interval_seconds" in changed:
        changed["next_check_at"] = now
    return _change(
        repo, monitor_id, changed, now=now,
        reset_state=retarget, close_reason=CloseReason.EDITED if retarget else None,
    )


def pause_monitor(repo: UptimeRepoPort, monitor_id: int, now: datetime) -> Monitor:
    """Pause and reset state, closing the open incident ("paused"); no-op if paused."""
    current = get_monitor(repo, monitor_id)
    if current.is_paused:
        return current
    return _change(
        repo, monitor_id, {"is_paused": True}, now=now,
        reset_state=True, close_reason=CloseReason.PAUSED,
    )


def resume_monitor(repo: UptimeRepoPort, monitor_id: int, now: datetime) -> Monitor:
    """Unpause with a fresh state and check at once; no-op if not paused."""
    current = get_monitor(repo, monitor_id)
    if not current.is_paused:
        return current
    return _change(
        repo, monitor_id, {"is_paused": False, "next_check_at": now}, now=now, reset_state=True,
    )


def delete_monitor(repo: UptimeRepoPort, monitor_id: int) -> None:
    if not repo.delete_monitor(monitor_id):
        raise MonitorNotFound(f"monitor {monitor_id} not found")


def _change(repo: UptimeRepoPort, monitor_id: int, values: dict[str, Any], **kwargs) -> Monitor:
    monitor = repo.change_monitor(monitor_id, values, **kwargs)
    if monitor is None:
        raise MonitorNotFound(f"monitor {monitor_id} not found")
    return monitor


def is_down(monitor: Monitor) -> bool:
    return not monitor.is_paused and monitor.state == MonitorState.DOWN
