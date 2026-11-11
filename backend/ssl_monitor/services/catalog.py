"""Customers and monitor groups — plain functions over the storage ports.

A group bundles a customer's SSL domains and website monitors; its health is the
worst of its members. ``add_member`` is the "one-click add": from a hostname or
URL it creates (or moves) the SSL domain and creates the website monitor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..adapters.storage.base import CatalogRepoPort, StoragePort, UptimeRepoPort
from ..core.catalog_models import Customer, MonitorGroup
from ..core.models import DomainRecord, Status, make_domain_key
from ..core.uptime_models import Monitor
from . import monitors as monitor_service
from .alert_rules import is_valid_email
from .domains import DomainNotFound, InvalidDomain, add_domain
from .errors import CustomerNotFound, GroupNotFound, InvalidInput
from .monitor_validation import UrlCheck, has_control_characters
from .monitors import require_customer, require_group

MAX_NAME_LENGTH = 200
_SSL_DOWN = frozenset({Status.EXPIRED, Status.UNREACHABLE})

# Probes a freshly added domain in place (like POST /domains does).
RefreshFn = Callable[[DomainRecord], None]


@dataclass(frozen=True)
class Stores:
    """The three repositories a group spans."""

    domains: StoragePort
    monitors: UptimeRepoPort
    catalog: CatalogRepoPort


@dataclass(frozen=True)
class CustomerView:
    customer: Customer
    group_count: int


@dataclass(frozen=True)
class GroupSummary:
    group: MonitorGroup
    customer_name: str | None
    ssl_count: int
    website_count: int
    health: str
    problems: int


@dataclass(frozen=True)
class GroupDetail:
    summary: GroupSummary
    domains: list[DomainRecord]
    monitors: list[Monitor]


@dataclass(frozen=True)
class MemberRequest:
    target: str
    add_ssl: bool = True
    add_website: bool = True
    interval_seconds: int = 300


@dataclass
class MemberResult:
    domain: DomainRecord | None = None
    monitor: Monitor | None = None
    messages: list[str] = field(default_factory=list)


# ── Validation ───────────────────────────────────────────────────


def _clean_name(value: Any, what: str) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name:
        raise InvalidInput(f"{what} name must not be blank")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidInput(f"{what} name must be at most {MAX_NAME_LENGTH} characters")
    if has_control_characters(name):
        raise InvalidInput(f"{what} name must not contain line breaks or control characters")
    return name


def _clean_email(value: Any) -> str | None:
    email = (value or "").strip()
    if not email:
        return None
    if not is_valid_email(email):
        raise InvalidInput(f"invalid contact email: {email!r}")
    return email


# ── Customers ────────────────────────────────────────────────────


def list_customers(catalog: CatalogRepoPort) -> list[CustomerView]:
    groups = catalog.list_groups()
    return [
        CustomerView(c, sum(1 for g in groups if g.customer_id == c.id))
        for c in catalog.list_customers()
    ]


def describe_customer(catalog: CatalogRepoPort, customer: Customer) -> CustomerView:
    return CustomerView(customer, len(catalog.list_groups(customer.id)))


def create_customer(
    catalog: CatalogRepoPort, name: Any, contact_email: Any = None
) -> Customer:
    return catalog.add_customer(_clean_name(name, "customer"), _clean_email(contact_email))


def update_customer(
    catalog: CatalogRepoPort, customer_id: int, fields: Mapping[str, Any]
) -> Customer:
    values: dict[str, Any] = {}
    if "name" in fields:
        values["name"] = _clean_name(fields["name"], "customer")
    if "contact_email" in fields:
        values["contact_email"] = _clean_email(fields["contact_email"])
    customer = catalog.update_customer(customer_id, values) if values else (
        catalog.get_customer(customer_id)
    )
    if customer is None:
        raise CustomerNotFound(f"customer {customer_id} not found")
    return customer


def delete_customer(catalog: CatalogRepoPort, customer_id: int) -> None:
    """Deletes the customer's groups too; their members are ungrouped, not deleted."""
    if not catalog.delete_customer(customer_id):
        raise CustomerNotFound(f"customer {customer_id} not found")


# ── Groups ───────────────────────────────────────────────────────


def group_health(domains: list[DomainRecord], monitors: list[Monitor]) -> tuple[str, int]:
    """(health, problem count). Worst wins; paused/unknown websites are not problems."""
    sites_down = sum(1 for m in monitors if monitor_service.is_down(m))
    ssl_down = sum(1 for d in domains if d.status in _SSL_DOWN)
    ssl_warning = sum(1 for d in domains if d.status == Status.WARNING)
    problems = sites_down + ssl_down + ssl_warning
    if sites_down or ssl_down:
        return "down", problems
    if ssl_warning:
        return "warning", problems
    if not domains and not monitors:
        return "empty", 0
    return "ok", 0


def _summarise(
    group: MonitorGroup,
    customer_names: Mapping[int, str],
    domains: list[DomainRecord],
    monitors: list[Monitor],
) -> GroupSummary:
    health, problems = group_health(domains, monitors)
    return GroupSummary(
        group=group,
        customer_name=customer_names.get(group.customer_id),
        ssl_count=len(domains),
        website_count=len(monitors),
        health=health,
        problems=problems,
    )


def _customer_names(catalog: CatalogRepoPort) -> dict[int, str]:
    return {c.id: c.name for c in catalog.list_customers()}


def list_groups(stores: Stores, customer_id: int | None = None) -> list[GroupSummary]:
    """Group summaries, ordered by customer name then group name."""
    names = _customer_names(stores.catalog)
    all_domains = stores.domains.list()
    all_monitors = stores.monitors.list_monitors()
    summaries = [
        _summarise(
            g, names,
            [d for d in all_domains if d.group_id == g.id],
            [m for m in all_monitors if m.group_id == g.id],
        )
        for g in stores.catalog.list_groups(customer_id)
    ]
    return sorted(summaries, key=lambda s: ((s.customer_name or "").lower(), s.group.name.lower()))


def _members(stores: Stores, group_id: int) -> tuple[list[DomainRecord], list[Monitor]]:
    domains = sorted(
        (d for d in stores.domains.list() if d.group_id == group_id), key=lambda d: d.domain
    )
    return domains, monitor_service.list_monitors(stores.monitors, group_id=group_id)


def summarise_group(stores: Stores, group: MonitorGroup) -> GroupSummary:
    domains, monitors = _members(stores, group.id)
    return _summarise(group, _customer_names(stores.catalog), domains, monitors)


def get_group_detail(stores: Stores, group_id: int) -> GroupDetail:
    group = require_group(stores.catalog, group_id)
    domains, monitors = _members(stores, group_id)
    summary = _summarise(group, _customer_names(stores.catalog), domains, monitors)
    return GroupDetail(summary=summary, domains=domains, monitors=monitors)


def create_group(catalog: CatalogRepoPort, customer_id: int, name: Any) -> MonitorGroup:
    require_customer(catalog, customer_id)
    return catalog.add_group(customer_id, _clean_name(name, "group"))


def rename_group(catalog: CatalogRepoPort, group_id: int, name: Any) -> MonitorGroup:
    group = catalog.rename_group(group_id, _clean_name(name, "group"))
    if group is None:
        raise GroupNotFound(f"group {group_id} not found")
    return group


def delete_group(catalog: CatalogRepoPort, group_id: int) -> None:
    """Members are ungrouped, never deleted."""
    if not catalog.delete_group(group_id):
        raise GroupNotFound(f"group {group_id} not found")


# ── Domain membership ────────────────────────────────────────────


def set_domain_group(stores: Stores, domain: str, group_id: int | None) -> DomainRecord:
    """Move an SSL domain into a group (taking its customer), or ungroup it
    (``None``; the customer is kept)."""
    record = stores.domains.get(domain)
    if record is None:
        raise DomainNotFound(f"not monitored: {domain}")
    if group_id is None:
        record.group_id = None
    else:
        _place_in_group(record, require_group(stores.catalog, group_id))
    stores.domains.put(record)
    return record


def _place_in_group(record: DomainRecord, group: MonitorGroup) -> None:
    record.group_id = group.id
    record.customer_id = group.customer_id


# ── One-click add ────────────────────────────────────────────────


@dataclass(frozen=True)
class _Target:
    host: str
    ssl_port: int
    url: str


def _parse_target(target: str) -> _Target:
    """A bare ``host[:port][/path]`` is taken as ``https://host[:port][/path]``."""
    target = (target or "").strip()
    if not target:
        raise InvalidInput("target must not be blank")
    if "://" not in target:
        target = f"https://{target.rstrip('/')}"
    try:
        parts = urlsplit(target)
        port = parts.port
    except ValueError as exc:
        raise InvalidInput(f"invalid URL: {exc}") from exc
    if not parts.hostname:
        raise InvalidInput("target URL must include a host name")
    ssl_port = port if parts.scheme.lower() == "https" and port else 443
    return _Target(host=parts.hostname, ssl_port=ssl_port, url=target)


def add_member(
    stores: Stores,
    group_id: int,
    request: MemberRequest,
    *,
    check_url: UrlCheck,
    refresh: RefreshFn,
    now: datetime,
) -> MemberResult:
    """Add a host to a group as an SSL domain and/or a website monitor. A domain or
    website that is already monitored is moved into the group, never duplicated.

    Everything is validated before anything is written, so a rejected URL never
    leaves a half-added member behind."""
    if not (request.add_ssl or request.add_website):
        raise InvalidInput("choose at least one of SSL certificate or website uptime")
    group = require_group(stores.catalog, group_id)
    target = _parse_target(request.target)
    monitor = _prepare_website(stores, group, target, request, check_url, now) if (
        request.add_website
    ) else None
    result = MemberResult()
    if request.add_ssl:
        result.domain = _add_or_move_domain(stores, group, target, refresh, now, result.messages)
    if monitor is not None:
        result.monitor = _add_or_move_monitor(stores, group, monitor, now, result.messages)
    return result


def _prepare_website(
    stores: Stores,
    group: MonitorGroup,
    target: _Target,
    request: MemberRequest,
    check_url: UrlCheck,
    now: datetime,
) -> Monitor:
    fields = {"name": target.host, "url": target.url,
              "interval_seconds": request.interval_seconds, "group_id": group.id}
    return monitor_service.prepare_monitor(stores.catalog, fields, check_url=check_url, now=now)


def _add_or_move_domain(
    stores: Stores,
    group: MonitorGroup,
    target: _Target,
    refresh: RefreshFn,
    now: datetime,
    messages: list[str],
) -> DomainRecord:
    key = make_domain_key(target.host, target.ssl_port)
    existing = stores.domains.get(key)
    if existing is not None:
        _place_in_group(existing, group)
        stores.domains.put(existing)
        messages.append(_moved_message(key, group))
        return existing
    try:
        record = add_domain(stores.domains, target.host, target.ssl_port, now)
    except InvalidDomain as exc:
        raise InvalidInput(str(exc)) from exc
    _place_in_group(record, group)
    refresh(record)
    stores.domains.put(record)
    return record


def _add_or_move_monitor(
    stores: Stores, group: MonitorGroup, monitor: Monitor, now: datetime, messages: list[str]
) -> Monitor:
    existing = _find_monitor_by_url(stores.monitors, monitor.url)
    if existing is None:
        return stores.monitors.add_monitor(monitor)
    moved = stores.monitors.change_monitor(
        existing.id, {"group_id": group.id, "customer_id": group.customer_id}, now=now
    )
    if moved is None:  # deleted in the meantime
        return stores.monitors.add_monitor(monitor)
    messages.append(_moved_message(existing.url, group))
    return moved


def _find_monitor_by_url(monitors: UptimeRepoPort, url: str) -> Monitor | None:
    key = _url_key(url)
    return next((m for m in monitors.list_monitors() if _url_key(m.url) == key), None)


def _url_key(url: str) -> str:
    """Normalised URLs compare equal with or without a bare trailing slash."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=parts.path or "/"))


def _moved_message(what: str, group: MonitorGroup) -> str:
    return f"{what} was already monitored — moved into {group.name}"


def ungroup_members(
    domains: StoragePort, monitors: UptimeRepoPort, group_ids: set[int], now: datetime
) -> None:
    """Clear ``group_id`` on every domain and monitor in ``group_ids`` — what the
    Postgres foreign keys do on group deletion, for the in-memory stores."""
    for record in domains.list():
        if record.group_id in group_ids:
            domains.put(replace(record, group_id=None))
    for monitor in monitors.list_monitors():
        if monitor.group_id in group_ids:
            monitors.change_monitor(monitor.id, {"group_id": None}, now=now)


def unassign_customer(
    domains: StoragePort, monitors: UptimeRepoPort, customer_id: int, now: datetime
) -> None:
    """Clear ``customer_id`` on every domain and monitor of a deleted customer — what
    the Postgres foreign keys do on customer deletion, for the in-memory stores."""
    for record in domains.list():
        if record.customer_id == customer_id:
            domains.put(replace(record, customer_id=None))
    for monitor in monitors.list_monitors():
        if monitor.customer_id == customer_id:
            monitors.change_monitor(monitor.id, {"customer_id": None}, now=now)
