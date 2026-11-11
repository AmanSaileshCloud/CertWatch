"""Customers (the top of Customer → Group → members)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from ...adapters.storage.base import CatalogRepoPort
from ...services import catalog as service
from ..dependencies import get_catalog_repo, get_current_user
from ..uptime_schemas import CustomerIn, CustomerOut, CustomerPatch, provided_fields

router = APIRouter(prefix="/customers", tags=["customers"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[CustomerOut])
def list_customers(catalog: CatalogRepoPort = Depends(get_catalog_repo)) -> list[CustomerOut]:
    return [CustomerOut.from_view(v) for v in service.list_customers(catalog)]


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    body: CustomerIn, catalog: CatalogRepoPort = Depends(get_catalog_repo)
) -> CustomerOut:
    customer = service.create_customer(catalog, body.name, body.contact_email)
    return CustomerOut.from_view(service.describe_customer(catalog, customer))


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int, body: CustomerPatch, catalog: CatalogRepoPort = Depends(get_catalog_repo)
) -> CustomerOut:
    customer = service.update_customer(catalog, customer_id, provided_fields(body))
    return CustomerOut.from_view(service.describe_customer(catalog, customer))


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(
    customer_id: int, catalog: CatalogRepoPort = Depends(get_catalog_repo)
) -> Response:
    service.delete_customer(catalog, customer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
