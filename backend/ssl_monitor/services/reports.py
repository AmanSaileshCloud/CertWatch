"""Uptime reports: uptime % per website and per customer, and the downtime log.

The numbers come from ``core.reports`` (time-based uptime from incidents) plus
the check statistics (response times) stored by the uptime worker.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..adapters.storage.base import CatalogRepoPort, UptimeRepoPort
from ..core.reports import (
    STATS_UNIT,
    CheckStats,
    Granularity,
    Period,
    downtime_seconds,
    monitored_seconds,
    period_key,
    periods,
    range_problem,
    uptime_percent,
)
from ..core.uptime_models import Incident, Monitor
from .errors import InvalidInput


@dataclass(frozen=True)
class PeriodFigures:
    uptime_pct: float | None
    downtime_seconds: int
    monitored_seconds: int
    incidents: int  # outages that started in this period
    checks: int
    failed_checks: int
    avg_response_ms: int | None


@dataclass(frozen=True)
class MonitorReport:
    monitor_id: int
    name: str
    url: str
    customer_id: int | None
    customer_name: str | None
    group_name: str | None
    periods: list[PeriodFigures]
    total: PeriodFigures


@dataclass(frozen=True)
class CustomerReport:
    customer_id: int | None  # None = websites not assigned to a customer
    customer_name: str
    monitors: int
    periods: list[PeriodFigures]
    total: PeriodFigures


@dataclass(frozen=True)
class UptimeReport:
    first: date
    last: date
    granularity: Granularity
    timezone: str
    periods: list[Period]
    monitors: list[MonitorReport] = field(default_factory=list)
    customers: list[CustomerReport] = field(default_factory=list)


@dataclass(frozen=True)
class DowntimeEntry:
    incident_id: int
    monitor_id: int
    monitor_name: str
    url: str
    customer_name: str | None
    group_name: str | None
    started_at: datetime
    resolved_at: datetime | None
    downtime_seconds: int  # inside the report range
    reason: str | None
    close_reason: str | None


UNASSIGNED = "No customer"


def _validate(first: date, last: date, granularity: Granularity, tz: ZoneInfo,
              now: datetime) -> None:
    problem = range_problem(first, last, granularity, now.astimezone(tz).date())
    if problem:
        raise InvalidInput(problem)


def _selected_monitors(repo: UptimeRepoPort, customer_id: int | None,
                       group_id: int | None) -> list[Monitor]:
    return [m for m in repo.list_monitors()
            if (customer_id is None or m.customer_id == customer_id)
            and (group_id is None or m.group_id == group_id)]


def _names(catalog: CatalogRepoPort) -> tuple[dict[int, str], dict[int, str]]:
    return ({c.id: c.name for c in catalog.list_customers()},
            {g.id: g.name for g in catalog.list_groups()})


def _incidents_by_monitor(repo: UptimeRepoPort, start: datetime,
                          end: datetime) -> dict[int, list[Incident]]:
    by_monitor: dict[int, list[Incident]] = defaultdict(list)
    for incident in repo.incidents_between(start, end):
        by_monitor[incident.monitor_id].append(incident)
    return by_monitor


def _stats_by_period(repo: UptimeRepoPort, start: datetime, end: datetime, tz: str,
                     granularity: Granularity, first: date) -> dict[tuple[int, date], CheckStats]:
    folded: dict[tuple[int, date], CheckStats] = {}
    raw = repo.check_stats(start, end, tz=tz, unit=STATS_UNIT[granularity.value])
    for (monitor_id, day), stats in raw.items():
        key = (monitor_id, period_key(max(day, first), granularity, first))
        folded[key] = folded.get(key, CheckStats()) + stats
    return folded


def _figures(downtime: float, monitored: float, incidents: int,
             stats: CheckStats) -> PeriodFigures:
    return PeriodFigures(
        uptime_pct=uptime_percent(downtime, monitored),
        downtime_seconds=round(downtime),
        monitored_seconds=round(monitored),
        incidents=incidents,
        checks=stats.checks,
        failed_checks=stats.checks - stats.up_checks,
        avg_response_ms=stats.avg_response_ms,
    )


@dataclass
class _Totals:
    """Running sums used to build an aggregate (whole range, or a whole customer)."""

    downtime: float = 0.0
    monitored: float = 0.0
    incidents: int = 0
    stats: CheckStats = field(default_factory=CheckStats)

    def add(self, downtime: float, monitored: float, incidents: int, stats: CheckStats) -> None:
        self.downtime += downtime
        self.monitored += monitored
        self.incidents += incidents
        self.stats = self.stats + stats

    def figures(self) -> PeriodFigures:
        return _figures(self.downtime, self.monitored, self.incidents, self.stats)


def uptime_report(
    repo: UptimeRepoPort,
    catalog: CatalogRepoPort,
    *,
    first: date,
    last: date,
    granularity: Granularity,
    tz: str,
    now: datetime,
    customer_id: int | None = None,
    group_id: int | None = None,
) -> UptimeReport:
    """Uptime per website and per customer for each period from ``first`` to
    ``last`` (local dates in ``tz``, both inclusive)."""
    zone = ZoneInfo(tz)
    _validate(first, last, granularity, zone, now)
    spans = periods(first, last, granularity, zone)
    start, end = spans[0].start, spans[-1].end
    monitors = _selected_monitors(repo, customer_id, group_id)
    customers, groups = _names(catalog)
    incidents = _incidents_by_monitor(repo, start, end)
    stats = _stats_by_period(repo, start, end, tz, granularity, first)

    monitor_reports: list[MonitorReport] = []
    customer_periods: dict[int | None, list[_Totals]] = {}
    customer_totals: dict[int | None, _Totals] = {}
    customer_counts: dict[int | None, int] = defaultdict(int)

    for m in monitors:
        mine = incidents.get(m.id, [])
        per_customer = customer_periods.setdefault(m.customer_id, [_Totals() for _ in spans])
        overall = _Totals()
        rows = []
        for i, span in enumerate(spans):
            down = downtime_seconds(mine, span.start, span.end, now)
            watched = monitored_seconds(m.created_at, span.start, span.end, now)
            started = sum(1 for inc in mine if span.start <= inc.started_at < span.end)
            st = stats.get((m.id, span.key), CheckStats())
            rows.append(_figures(down, watched, started, st))
            overall.add(down, watched, started, st)
            per_customer[i].add(down, watched, started, st)
        customer_totals.setdefault(m.customer_id, _Totals()).add(
            overall.downtime, overall.monitored, overall.incidents, overall.stats)
        customer_counts[m.customer_id] += 1
        monitor_reports.append(MonitorReport(
            monitor_id=m.id, name=m.name, url=m.url, customer_id=m.customer_id,
            customer_name=customers.get(m.customer_id) if m.customer_id else None,
            group_name=groups.get(m.group_id) if m.group_id else None,
            periods=rows, total=overall.figures(),
        ))

    customer_reports = sorted(
        (CustomerReport(
            customer_id=cid,
            customer_name=customers.get(cid, UNASSIGNED) if cid else UNASSIGNED,
            monitors=customer_counts[cid],
            periods=[t.figures() for t in customer_periods[cid]],
            total=customer_totals[cid].figures(),
        ) for cid in customer_periods),
        key=lambda c: (c.customer_id is None, c.customer_name.lower()),
    )
    monitor_reports.sort(key=lambda r: ((r.customer_name or "~").lower(), r.name.lower()))
    return UptimeReport(first=first, last=last, granularity=granularity, timezone=tz,
                        periods=spans, monitors=monitor_reports, customers=customer_reports)


def downtime_log(
    repo: UptimeRepoPort,
    catalog: CatalogRepoPort,
    *,
    first: date,
    last: date,
    tz: str,
    now: datetime,
    customer_id: int | None = None,
    group_id: int | None = None,
) -> list[DowntimeEntry]:
    """Every outage that overlaps the range, newest first."""
    zone = ZoneInfo(tz)
    _validate(first, last, Granularity.TOTAL, zone, now)
    [span] = periods(first, last, Granularity.TOTAL, zone)
    monitors = {m.id: m for m in _selected_monitors(repo, customer_id, group_id)}
    customers, groups = _names(catalog)
    entries = []
    for incident in repo.incidents_between(span.start, span.end):
        m = monitors.get(incident.monitor_id)
        if m is None:
            continue
        entries.append(DowntimeEntry(
            incident_id=incident.id, monitor_id=m.id, monitor_name=m.name, url=m.url,
            customer_name=customers.get(m.customer_id) if m.customer_id else None,
            group_name=groups.get(m.group_id) if m.group_id else None,
            started_at=incident.started_at, resolved_at=incident.resolved_at,
            downtime_seconds=round(downtime_seconds([incident], span.start, span.end, now)),
            reason=incident.reason,
            close_reason=incident.close_reason.value if incident.close_reason else None,
        ))
    entries.sort(key=lambda e: e.started_at, reverse=True)
    return entries


def default_range(now: datetime, tz: str, days: int = 30) -> tuple[date, date]:
    """The last ``days`` days up to today (local)."""
    today = now.astimezone(ZoneInfo(tz)).date()
    return today - timedelta(days=days - 1), today
