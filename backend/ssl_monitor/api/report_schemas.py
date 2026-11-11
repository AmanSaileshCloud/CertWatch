"""Response shapes for /reports (built from the services.reports dataclasses)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class _FromAttributes(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PeriodOut(_FromAttributes):
    key: date
    label: str
    start: datetime
    end: datetime


class FiguresOut(_FromAttributes):
    uptime_pct: float | None
    downtime_seconds: int
    monitored_seconds: int
    incidents: int
    checks: int
    failed_checks: int
    avg_response_ms: int | None


class MonitorReportOut(_FromAttributes):
    monitor_id: int
    name: str
    url: str
    customer_id: int | None
    customer_name: str | None
    group_name: str | None
    periods: list[FiguresOut]
    total: FiguresOut


class CustomerReportOut(_FromAttributes):
    customer_id: int | None
    customer_name: str
    monitors: int
    periods: list[FiguresOut]
    total: FiguresOut


class UptimeReportOut(_FromAttributes):
    first: date
    last: date
    granularity: str
    timezone: str
    periods: list[PeriodOut]
    monitors: list[MonitorReportOut]
    customers: list[CustomerReportOut]


class DowntimeEntryOut(_FromAttributes):
    incident_id: int
    monitor_id: int
    monitor_name: str
    url: str
    customer_name: str | None
    group_name: str | None
    started_at: datetime
    resolved_at: datetime | None
    downtime_seconds: int
    reason: str | None
    close_reason: str | None
