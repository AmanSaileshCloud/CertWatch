"""Core domain models. Pure data — no AWS, no I/O, no env reads.

These types are shared across the cert probe, threshold logic, status
classification, storage adapter, and notifier adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    """Health of a monitored domain's certificate.

    Inherits from ``str`` so the value serializes directly to JSON / storage.
    """

    OK = "ok"
    WARNING = "warning"
    EXPIRED = "expired"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single TLS probe.

    Discriminated by ``reachable``. When the host could not be reached (refused,
    timeout, DNS failure, TLS handshake failure) ``reachable`` is False,
    ``not_after`` is None, and ``error`` carries a short reason. A reachable
    result always carries a UTC-aware ``not_after`` — even for an already-expired
    or self-signed certificate.
    """

    host: str
    port: int
    reachable: bool
    not_after: datetime | None = None  # UTC-aware
    error: str | None = None


@dataclass(frozen=True)
class CertInfo:
    """Human-readable details of a leaf TLS certificate (for the detail view)."""

    issuer: str
    subject: str
    serial: str
    sig_algorithm: str
    key_type: str
    key_bits: int | None
    not_before: datetime | None
    not_after: datetime | None
    sans: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AlertDecision:
    """Result of :func:`decide_alert`.

    ``new_last_alert`` is the value the caller should persist as the domain's
    ``last_alert_threshold`` after acting on this decision (whether or not an
    alert fired — e.g. a renewal resets it to None).
    """

    should_alert: bool
    threshold: int | None
    new_last_alert: int | None


@dataclass(frozen=True)
class Alert:
    """A notification payload handed to a notifier adapter.

    The notifier sends to its global recipient (e.g. ``ALERT_EMAIL``).
    """

    domain: str
    status: Status
    days_remaining: int | None
    threshold: int | None
    not_after: datetime | None = None


def make_domain_key(host: str, port: int = 443) -> str:
    """Canonical primary key for a monitored target: ``host:port``."""
    return f"{host}:{port}"


@dataclass
class DomainRecord:
    """The stored state of one monitored domain (persisted record).

    ``domain`` is the partition key (``host:port``). All timestamps are
    UTC-aware datetimes in memory and serialize to ISO8601 strings at the
    storage boundary. This type is pure: serialization lives in the storage
    adapter, not here.
    """

    domain: str
    host: str
    port: int
    created_at: datetime
    not_after: datetime | None = None
    days_remaining: int | None = None
    status: Status = Status.UNREACHABLE
    last_checked_at: datetime | None = None
    last_error: str | None = None
    last_alert_threshold: int | None = None
    # Per-domain notification config
    alerts_enabled: bool = True

    @classmethod
    def new(cls, host: str, port: int, created_at: datetime) -> DomainRecord:
        """Create a freshly-added domain that has not been checked yet."""
        return cls(
            domain=make_domain_key(host, port),
            host=host,
            port=port,
            created_at=created_at,
        )
