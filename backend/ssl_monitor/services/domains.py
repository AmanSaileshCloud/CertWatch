"""Domain CRUD service — plain functions over the storage port.

Both the FastAPI routes and the Lambda handlers call these, so validation and
business rules live in exactly one place. No web framework, no AWS here.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..adapters.storage.base import StoragePort
from ..core.models import DomainRecord, make_domain_key

# Hostname (RFC-1123-ish) or IPv4 literal. Deliberately strict: no scheme, no
# path, no spaces, no embedded port — port is a separate field.
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip().lower()))


class DomainError(ValueError):
    """Base for domain validation/state errors."""


class InvalidDomain(DomainError):
    pass


class DomainExists(DomainError):
    pass


class DomainNotFound(DomainError):
    pass


class InvalidEmail(DomainError):
    pass


def _validate(host: str, port: int) -> tuple[str, int]:
    host = host.strip().lower()
    if not host:
        raise InvalidDomain("host must not be empty")
    if "://" in host or "/" in host or ":" in host or " " in host:
        raise InvalidDomain("host must be a bare hostname (no scheme, path, or port)")
    if not _HOST_RE.match(host):
        raise InvalidDomain(f"invalid hostname: {host!r}")
    if not (1 <= port <= 65535):
        raise InvalidDomain(f"port out of range: {port}")
    return host, port


def add_domain(storage: StoragePort, host: str, port: int, now: datetime) -> DomainRecord:
    """Validate and insert a new domain. Raises DomainExists if already present."""
    host, port = _validate(host, port)
    key = make_domain_key(host, port)
    if storage.get(key) is not None:
        raise DomainExists(f"already monitored: {key}")
    record = DomainRecord.new(host=host, port=port, created_at=now)
    storage.put(record)
    return record


def list_domains(storage: StoragePort) -> list[DomainRecord]:
    return sorted(storage.list(), key=lambda r: r.domain)


def delete_domain(storage: StoragePort, domain: str) -> bool:
    """Delete by key. Returns whether it existed; deletion is idempotent."""
    existed = storage.get(domain) is not None
    storage.delete(domain)
    return existed


def _normalize_emails(emails: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in emails:
        email = raw.strip().lower()
        if not email:
            continue
        if not is_valid_email(email):
            raise InvalidEmail(f"invalid email: {raw!r}")
        if email not in cleaned:
            cleaned.append(email)
    return cleaned


def update_domain(
    storage: StoragePort,
    domain: str,
    *,
    notify_emails: list[str] | None = None,
    alerts_enabled: bool | None = None,
) -> DomainRecord:
    """Update a domain's notification config. Only provided fields change."""
    record = storage.get(domain)
    if record is None:
        raise DomainNotFound(f"not monitored: {domain}")
    if notify_emails is not None:
        record.notify_emails = _normalize_emails(notify_emails)
    if alerts_enabled is not None:
        record.alerts_enabled = alerts_enabled
    storage.put(record)
    return record
