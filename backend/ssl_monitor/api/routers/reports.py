"""Uptime reports: uptime % per website / customer, downtime log, CSV export."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from ...config.settings import Settings
from ...core.reports import Granularity
from ...services import reports as service
from ...services.catalog import Stores
from ...services.report_csv import downtime_csv, uptime_csv
from ..dependencies import get_app_settings, get_current_user
from ..report_schemas import DowntimeEntryOut, UptimeReportOut
from . import get_stores, utc_now

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(get_current_user)])


class _Range:
    """Shared query parameters. Dates are local to REPORT_TIMEZONE and inclusive;
    without them the report covers the last 30 days."""

    def __init__(
        self,
        start: date | None = Query(None, description="first day, YYYY-MM-DD"),
        end: date | None = Query(None, description="last day (inclusive), YYYY-MM-DD"),
        customer_id: int | None = None,
        group_id: int | None = None,
        settings: Settings = Depends(get_app_settings),
    ) -> None:
        self.now = utc_now()
        self.tz = settings.report_timezone
        default_first, default_last = service.default_range(self.now, self.tz)
        self.first = start or default_first
        self.last = end or default_last
        self.customer_id = customer_id
        self.group_id = group_id


def _report(stores: Stores, r: _Range, granularity: Granularity) -> service.UptimeReport:
    return service.uptime_report(
        stores.monitors, stores.catalog, first=r.first, last=r.last, granularity=granularity,
        tz=r.tz, now=r.now, customer_id=r.customer_id, group_id=r.group_id,
    )


def _log(stores: Stores, r: _Range) -> list[service.DowntimeEntry]:
    return service.downtime_log(
        stores.monitors, stores.catalog, first=r.first, last=r.last, tz=r.tz, now=r.now,
        customer_id=r.customer_id, group_id=r.group_id,
    )


def _csv(body: str, name: str, r: _Range) -> Response:
    filename = f"certwatch-{name}-{r.first.isoformat()}-to-{r.last.isoformat()}.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/uptime", response_model=UptimeReportOut)
def uptime_report(
    granularity: Granularity = Granularity.DAY,
    r: _Range = Depends(),
    stores: Stores = Depends(get_stores),
) -> UptimeReportOut:
    return UptimeReportOut.model_validate(_report(stores, r, granularity))


@router.get("/uptime.csv", response_class=Response)
def uptime_report_csv(
    granularity: Granularity = Granularity.DAY,
    level: Literal["monitor", "customer"] = "monitor",
    r: _Range = Depends(),
    stores: Stores = Depends(get_stores),
) -> Response:
    report = _report(stores, r, granularity)
    return _csv(uptime_csv(report, level), f"uptime-by-{level}", r)


@router.get("/downtime", response_model=list[DowntimeEntryOut])
def downtime_log(r: _Range = Depends(), stores: Stores = Depends(get_stores)) -> list[DowntimeEntryOut]:
    return [DowntimeEntryOut.model_validate(e) for e in _log(stores, r)]


@router.get("/downtime.csv", response_class=Response)
def downtime_log_csv(r: _Range = Depends(), stores: Stores = Depends(get_stores)) -> Response:
    return _csv(downtime_csv(_log(stores, r), r.tz), "downtime", r)
