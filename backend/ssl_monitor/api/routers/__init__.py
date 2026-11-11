"""Routers for customers, groups and website monitors, plus what they share:
service-error → HTTP mapping and the dependencies that build service inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import partial

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from ...adapters.storage.base import CatalogRepoPort, DuplicateNameError, StoragePort, UptimeRepoPort
from ...config.settings import Settings
from ...core.models import DomainRecord
from ...core.uptime_models import Monitor
from ...services.catalog import RefreshFn, Stores
from ...services.checker import ProbeFn, refresh_status
from ...services.domains import DomainExists
from ...services.errors import InvalidInput, NotFound
from ...services.monitors import UrlCheck, describe_monitors
from ...services.url_policy import validate_monitor_url
from ..dependencies import (
    get_app_settings,
    get_catalog_repo,
    get_dns_resolver,
    get_probe,
    get_storage,
    get_uptime_repo,
)
from ..uptime_schemas import MonitorOut

_STATUS_BY_ERROR = (
    (NotFound, status.HTTP_404_NOT_FOUND),
    (DuplicateNameError, status.HTTP_409_CONFLICT),
    (DomainExists, status.HTTP_409_CONFLICT),  # one-click add racing another add
    (InvalidInput, status.HTTP_422_UNPROCESSABLE_CONTENT),
)


def install_error_handlers(app: FastAPI) -> None:
    """Turn service errors into ``{"detail": "<readable message>"}`` responses."""
    for error_type, status_code in _STATUS_BY_ERROR:
        app.add_exception_handler(error_type, partial(_error_response, status_code))


async def _error_response(status_code: int, _request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_stores(
    domains: StoragePort = Depends(get_storage),
    monitors: UptimeRepoPort = Depends(get_uptime_repo),
    catalog: CatalogRepoPort = Depends(get_catalog_repo),
) -> Stores:
    return Stores(domains=domains, monitors=monitors, catalog=catalog)


def get_url_check(
    settings: Settings = Depends(get_app_settings),
    resolve=Depends(get_dns_resolver),
) -> UrlCheck:
    return partial(
        validate_monitor_url, allow_private=settings.uptime_allow_private_targets, resolve=resolve
    )


def get_refresh(
    settings: Settings = Depends(get_app_settings),
    probe: ProbeFn = Depends(get_probe),
) -> RefreshFn:
    """Probe a new SSL domain at once, exactly like POST /domains."""

    def refresh(record: DomainRecord) -> None:
        refresh_status(record, now=utc_now(), thresholds=settings.threshold_days,
                       timeout=settings.tls_timeout_seconds, probe_fn=probe)

    return refresh


def monitors_out(stores: Stores, monitors: list[Monitor]) -> list[MonitorOut]:
    views = describe_monitors(stores.monitors, stores.catalog, monitors, utc_now())
    return [MonitorOut.from_view(v) for v in views]


def monitor_out(stores: Stores, monitor: Monitor) -> MonitorOut:
    return monitors_out(stores, [monitor])[0]
