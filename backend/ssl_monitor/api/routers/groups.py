"""Monitor groups: CRUD, health summaries and the one-click "add member"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from ...services import catalog as service
from ...services.catalog import MemberRequest, RefreshFn, Stores
from ...services.monitors import UrlCheck
from ..dependencies import get_current_user
from ..uptime_schemas import (
    AddMemberIn,
    AddMemberOut,
    GroupDetailOut,
    GroupIn,
    GroupPatch,
    GroupSummaryOut,
)
from . import get_refresh, get_stores, get_url_check, monitor_out, monitors_out, utc_now

router = APIRouter(prefix="/groups", tags=["groups"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[GroupSummaryOut])
def list_groups(
    customer_id: int | None = None, stores: Stores = Depends(get_stores)
) -> list[GroupSummaryOut]:
    return [GroupSummaryOut.from_summary(s) for s in service.list_groups(stores, customer_id)]


@router.post("", response_model=GroupSummaryOut, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupIn, stores: Stores = Depends(get_stores)) -> GroupSummaryOut:
    group = service.create_group(stores.catalog, body.customer_id, body.name)
    return GroupSummaryOut.from_summary(service.summarise_group(stores, group))


@router.get("/{group_id}", response_model=GroupDetailOut)
def get_group(group_id: int, stores: Stores = Depends(get_stores)) -> GroupDetailOut:
    detail = service.get_group_detail(stores, group_id)
    return GroupDetailOut.from_detail(detail, monitors_out(stores, detail.monitors))


@router.patch("/{group_id}", response_model=GroupSummaryOut)
def rename_group(
    group_id: int, body: GroupPatch, stores: Stores = Depends(get_stores)
) -> GroupSummaryOut:
    group = service.rename_group(stores.catalog, group_id, body.name)
    return GroupSummaryOut.from_summary(service.summarise_group(stores, group))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, stores: Stores = Depends(get_stores)) -> Response:
    service.delete_group(stores.catalog, group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{group_id}/members", response_model=AddMemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    group_id: int,
    body: AddMemberIn,
    stores: Stores = Depends(get_stores),
    check_url: UrlCheck = Depends(get_url_check),
    refresh: RefreshFn = Depends(get_refresh),
) -> AddMemberOut:
    result = service.add_member(
        stores, group_id, MemberRequest(**body.model_dump()),
        check_url=check_url, refresh=refresh, now=utc_now(),
    )
    monitor = monitor_out(stores, result.monitor) if result.monitor else None
    return AddMemberOut.from_result(result, monitor)
