"""Request/response models for customers, groups and website monitors (Pydantic v2).

Field rules (lengths, allowed intervals, URL safety) are checked by the services so
the error ``detail`` is one readable sentence; these models only fix the shapes.
PATCH bodies use ``model_fields_set`` to tell an absent field from an explicit null.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ..core.uptime_models import CheckResult, Incident
from ..services.catalog import CustomerView, GroupDetail, GroupSummary, MemberResult
from ..services.monitors import MonitorView
from .schemas import DomainOut


def provided_fields(body: BaseModel) -> dict[str, Any]:
    """Only the fields the client actually sent (explicit nulls included)."""
    return {name: getattr(body, name) for name in body.model_fields_set}


# ── Customers ────────────────────────────────────────────────────


class CustomerIn(BaseModel):
    name: str
    contact_email: str | None = None


class CustomerPatch(BaseModel):
    name: str | None = None
    contact_email: str | None = None


class CustomerOut(BaseModel):
    id: int
    name: str
    contact_email: str | None
    created_at: datetime | None
    group_count: int

    @classmethod
    def from_view(cls, view: CustomerView) -> CustomerOut:
        c = view.customer
        return cls(id=c.id, name=c.name, contact_email=c.contact_email,
                   created_at=c.created_at, group_count=view.group_count)


# ── Monitors ─────────────────────────────────────────────────────


class MonitorIn(BaseModel):
    name: str
    url: str
    interval_seconds: int = 300
    timeout_seconds: int = 10
    expected_status: int = 200
    keyword: str | None = None
    group_id: int | None = None
    customer_id: int | None = None


class MonitorPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    interval_seconds: int | None = None
    timeout_seconds: int | None = None
    expected_status: int | None = None
    keyword: str | None = None
    group_id: int | None = None
    customer_id: int | None = None


class MonitorOut(BaseModel):
    id: int
    name: str
    url: str
    type: str
    interval_seconds: int
    timeout_seconds: int
    expected_status: int
    keyword: str | None
    customer_id: int | None
    customer_name: str | None
    group_id: int | None
    group_name: str | None
    is_paused: bool
    status: str
    last_checked_at: datetime | None
    last_status_code: int | None
    last_response_ms: int | None
    last_error: str | None
    uptime_24h: float | None
    down_since: datetime | None
    created_at: datetime | None

    @classmethod
    def from_view(cls, view: MonitorView) -> MonitorOut:
        m = view.monitor
        return cls(
            id=m.id, name=m.name, url=m.url, type=m.type.value,
            interval_seconds=m.interval_seconds, timeout_seconds=m.timeout_seconds,
            expected_status=m.expected_status, keyword=m.keyword,
            customer_id=m.customer_id, customer_name=view.customer_name,
            group_id=m.group_id, group_name=view.group_name,
            is_paused=m.is_paused, status=view.status,
            last_checked_at=m.last_checked_at, last_status_code=m.last_status_code,
            last_response_ms=m.last_response_ms, last_error=m.last_error,
            uptime_24h=view.uptime_24h, down_since=view.down_since, created_at=m.created_at,
        )


class CheckOut(BaseModel):
    checked_at: datetime
    is_up: bool
    status_code: int | None
    response_ms: int | None
    error: str | None
    region: str

    @classmethod
    def from_result(cls, r: CheckResult) -> CheckOut:
        return cls(checked_at=r.checked_at, is_up=r.is_up, status_code=r.status_code,
                   response_ms=r.response_ms, error=r.error, region=r.region)


class IncidentOut(BaseModel):
    id: int
    monitor_id: int
    started_at: datetime
    resolved_at: datetime | None
    duration_seconds: int | None
    reason: str | None
    close_reason: str | None

    @classmethod
    def from_incident(cls, i: Incident) -> IncidentOut:
        return cls(
            id=i.id, monitor_id=i.monitor_id, started_at=i.started_at,
            resolved_at=i.resolved_at, duration_seconds=i.duration_seconds, reason=i.reason,
            close_reason=i.close_reason.value if i.close_reason else None,
        )


# ── Groups ───────────────────────────────────────────────────────


class GroupIn(BaseModel):
    customer_id: int
    name: str


class GroupPatch(BaseModel):
    name: str


class GroupSummaryOut(BaseModel):
    id: int
    customer_id: int
    customer_name: str | None
    name: str
    created_at: datetime | None
    ssl_count: int
    website_count: int
    health: str
    problems: int

    @classmethod
    def summary_fields(cls, s: GroupSummary) -> dict[str, Any]:
        g = s.group
        return dict(
            id=g.id, customer_id=g.customer_id, customer_name=s.customer_name, name=g.name,
            created_at=g.created_at, ssl_count=s.ssl_count, website_count=s.website_count,
            health=s.health, problems=s.problems,
        )

    @classmethod
    def from_summary(cls, s: GroupSummary) -> GroupSummaryOut:
        return cls(**cls.summary_fields(s))


class GroupDetailOut(GroupSummaryOut):
    domains: list[DomainOut]
    monitors: list[MonitorOut]

    @classmethod
    def from_detail(cls, detail: GroupDetail, monitors: list[MonitorOut]) -> GroupDetailOut:
        return cls(
            **cls.summary_fields(detail.summary),
            domains=[DomainOut.from_record(d) for d in detail.domains],
            monitors=monitors,
        )


class AddMemberIn(BaseModel):
    target: str
    add_ssl: bool = True
    add_website: bool = True
    interval_seconds: int = 300


class AddMemberOut(BaseModel):
    domain: DomainOut | None
    monitor: MonitorOut | None
    messages: list[str]

    @classmethod
    def from_result(cls, result: MemberResult, monitor: MonitorOut | None) -> AddMemberOut:
        domain = DomainOut.from_record(result.domain) if result.domain else None
        return cls(domain=domain, monitor=monitor, messages=result.messages)
