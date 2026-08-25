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
    """Outcome of a single TLS probe."""

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
    """Result of :func:`decide_alert`."""

    should_alert: bool
    threshold: int | None
    new_last_alert: int | None


@dataclass(frozen=True)
class Alert:
    """A notification payload handed to a notifier adapter."""

    domain: str
    status: Status
    days_remaining: int | None
    threshold: int | None
    not_after: datetime | None = None
    # Optional human explanation appended to the notification body. Used to say
    # *why* an alert fired when the status alone is ambiguous — e.g. an
    # UNREACHABLE that means "the agent stopped reporting", not "host is down".
    detail: str | None = None


def make_domain_key(host: str, port: int = 443) -> str:
    """Canonical primary key for a monitored target: ``host:port``."""
    return f"{host}:{port}"


# Who owns the truth for a record's certificate data.
SOURCE_DIRECT = "direct"  # the central checker probes it over the internet
SOURCE_AGENT = "agent"    # a remote agent probes it inside a private network


@dataclass
class DomainRecord:
    """The stored state of one monitored domain (persisted record)."""

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
    alerts_enabled: bool = True
    # Provenance. An agent-managed host lives inside a private network and is
    # unreachable from the central server, so the checker must NOT probe it —
    # doing so overwrites the agent's good data with a false "unreachable".
    source: str = SOURCE_DIRECT
    agent_id: str | None = None
    # Dedup flag for the "agent stopped reporting" alert, so a dead agent pages
    # once rather than on every checker pass. Cleared by the next fresh report.
    stale_alert_sent: bool = False

    @property
    def is_agent_managed(self) -> bool:
        return self.source == SOURCE_AGENT

    @classmethod
    def new(cls, host: str, port: int, created_at: datetime) -> DomainRecord:
        """Create a freshly-added domain that has not been checked yet."""
        return cls(
            domain=make_domain_key(host, port),
            host=host,
            port=port,
            created_at=created_at,
        )
