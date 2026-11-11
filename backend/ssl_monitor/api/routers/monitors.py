"""Website monitors: CRUD, pause/resume, check history and incidents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from ...services import monitors as service
from ...services.catalog import Stores
from ...services.monitors import UrlCheck
from ..dependencies import get_current_user
from ..uptime_schemas import CheckOut, IncidentOut, MonitorIn, MonitorOut, MonitorPatch, provided_fields
from . import get_stores, get_url_check, monitor_out, monitors_out, utc_now

router = APIRouter(prefix="/monitors", tags=["monitors"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[MonitorOut])
def list_monitors(
    group_id: int | None = None,
    customer_id: int | None = None,
    stores: Stores = Depends(get_stores),
) -> list[MonitorOut]:
    monitors = service.list_monitors(stores.monitors, group_id=group_id, customer_id=customer_id)
    return monitors_out(stores, monitors)


@router.post("", response_model=MonitorOut, status_code=status.HTTP_201_CREATED)
def create_monitor(
    body: MonitorIn,
    stores: Stores = Depends(get_stores),
    check_url: UrlCheck = Depends(get_url_check),
) -> MonitorOut:
    monitor = service.create_monitor(
        stores.monitors, stores.catalog, body.model_dump(), check_url=check_url, now=utc_now()
    )
    return monitor_out(stores, monitor)


@router.get("/{monitor_id}", response_model=MonitorOut)
def get_monitor(monitor_id: int, stores: Stores = Depends(get_stores)) -> MonitorOut:
    return monitor_out(stores, service.get_monitor(stores.monitors, monitor_id))


@router.patch("/{monitor_id}", response_model=MonitorOut)
def update_monitor(
    monitor_id: int,
    body: MonitorPatch,
    stores: Stores = Depends(get_stores),
    check_url: UrlCheck = Depends(get_url_check),
) -> MonitorOut:
    monitor = service.update_monitor(
        stores.monitors, stores.catalog, monitor_id, provided_fields(body),
        check_url=check_url, now=utc_now(),
    )
    return monitor_out(stores, monitor)


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_monitor(monitor_id: int, stores: Stores = Depends(get_stores)) -> Response:
    service.delete_monitor(stores.monitors, monitor_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{monitor_id}/pause", response_model=MonitorOut)
def pause_monitor(monitor_id: int, stores: Stores = Depends(get_stores)) -> MonitorOut:
    return monitor_out(stores, service.pause_monitor(stores.monitors, monitor_id, utc_now()))


@router.post("/{monitor_id}/resume", response_model=MonitorOut)
def resume_monitor(monitor_id: int, stores: Stores = Depends(get_stores)) -> MonitorOut:
    return monitor_out(stores, service.resume_monitor(stores.monitors, monitor_id, utc_now()))


@router.get("/{monitor_id}/checks", response_model=list[CheckOut])
def list_checks(
    monitor_id: int,
    limit: int = Query(50, ge=1, le=500),
    stores: Stores = Depends(get_stores),
) -> list[CheckOut]:
    results = service.list_checks(stores.monitors, monitor_id, limit)
    return [CheckOut.from_result(r) for r in results]


@router.get("/{monitor_id}/series", response_model=list[CheckOut])
def check_series(
    monitor_id: int,
    hours: int = Query(24, ge=1, le=168),
    stores: Stores = Depends(get_stores),
) -> list[CheckOut]:
    results = service.check_series(stores.monitors, monitor_id, hours, utc_now())
    return [CheckOut.from_result(r) for r in results]


@router.get("/{monitor_id}/incidents", response_model=list[IncidentOut])
def list_incidents(
    monitor_id: int,
    limit: int = Query(50, ge=1, le=500),
    stores: Stores = Depends(get_stores),
) -> list[IncidentOut]:
    incidents = service.list_incidents(stores.monitors, monitor_id, limit)
    return [IncidentOut.from_incident(i) for i in incidents]
