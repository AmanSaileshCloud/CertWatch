"""Shared fixtures for the customers / groups / monitors API tests.

The app is wired to in-memory repos, a healthy fake TLS probe and a fake DNS
resolver — no network, no database.
"""

from __future__ import annotations

import datetime as dt
import socket
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.storage.catalog_memory import MemoryCatalogRepo
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import (
    get_app_settings,
    get_catalog_repo,
    get_current_user,
    get_dns_resolver,
    get_probe,
    get_storage,
    get_uptime_repo,
)
from backend.ssl_monitor.auth.models import User
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.core.models import ProbeResult
from backend.ssl_monitor.core.uptime_models import CheckResult
from backend.ssl_monitor.services.catalog import unassign_customer, ungroup_members

UTC = dt.timezone.utc
PUBLIC_IP = "93.184.215.14"


@dataclass
class FakeDns:
    """host -> addresses; unknown hosts resolve to a public IP, ``missing`` ones fail."""

    hosts: dict[str, list[str]] = field(default_factory=lambda: {"localhost": ["127.0.0.1"]})
    missing: set[str] = field(default_factory=lambda: {"not-yet-live.example"})

    def __call__(self, host: str, port, *args, **kwargs):
        if host in self.missing:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                for ip in self.hosts.get(host, [PUBLIC_IP])]


@dataclass
class Api:
    client: TestClient
    storage: MemoryStorage
    repo: MemoryUptimeRepo
    catalog: MemoryCatalogRepo
    dns: FakeDns

    def record(self, monitor_id: int, *, up: bool, minutes_ago: float = 0) -> None:
        """Save one check result through the real incident rules."""
        result = CheckResult(
            monitor_id=monitor_id,
            checked_at=dt.datetime.now(UTC) - dt.timedelta(minutes=minutes_ago),
            is_up=up,
            status_code=200 if up else 503,
            response_ms=100,
            error=None if up else "Expected 200, got 503",
        )
        self.repo.record_checks([result], evaluate)

    def take_down(self, monitor_id: int) -> None:
        for _ in range(3):
            self.record(monitor_id, up=False)

    def customer(self, name: str = "Acme Corp") -> dict:
        r = self.client.post("/customers", json={"name": name})
        assert r.status_code == 201, r.text
        return r.json()

    def group(self, customer_id: int, name: str = "E-commerce") -> dict:
        r = self.client.post("/groups", json={"customer_id": customer_id, "name": name})
        assert r.status_code == 201, r.text
        return r.json()

    def monitor(self, **fields) -> dict:
        body = {"name": "Shop", "url": "https://shop.acme.com"} | fields
        r = self.client.post("/monitors", json=body)
        assert r.status_code == 201, r.text
        return r.json()


def _healthy_probe(host: str, port: int, timeout: float) -> ProbeResult:
    return ProbeResult(host, port, reachable=True,
                       not_after=dt.datetime.now(UTC) + dt.timedelta(days=90))


@pytest.fixture
def api():
    storage = MemoryStorage()
    repo = MemoryUptimeRepo()
    catalog = MemoryCatalogRepo(
        on_groups_deleted=lambda ids: ungroup_members(storage, repo, ids, dt.datetime.now(UTC)),
        on_customer_deleted=lambda cid: unassign_customer(storage, repo, cid,
                                                          dt.datetime.now(UTC)),
    )
    dns = FakeDns()
    settings = Settings.from_env(env={})
    overrides = {
        get_storage: lambda: storage,
        get_uptime_repo: lambda: repo,
        get_catalog_repo: lambda: catalog,
        get_dns_resolver: lambda: dns,
        get_app_settings: lambda: settings,
        get_probe: lambda: _healthy_probe,
        get_current_user: lambda: User("tester", "admin"),
    }
    app.dependency_overrides.update(overrides)
    yield Api(TestClient(app), storage, repo, catalog, dns)
    app.dependency_overrides.clear()
