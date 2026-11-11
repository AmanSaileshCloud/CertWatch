"""Customers / groups / monitors API end-to-end against the real PostgreSQL repos.

Only the TLS probe, DNS and auth are faked; every write goes through the Postgres
adapters, so the foreign-key behaviour (ungroup on group delete, cascade on
customer delete) and the change_monitor state rules are exercised for real.
"""

from __future__ import annotations

import datetime as dt
import socket

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.storage.catalog_postgres import PostgresCatalogRepo
from backend.ssl_monitor.adapters.storage.postgres import PostgresStorage
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
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

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc


def _resolve(host, port, *args, **kwargs):
    ip = "10.0.0.9" if host == "intranet.acme.com" else "93.184.215.14"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _probe(host, port, timeout):
    return ProbeResult(host, port, reachable=True,
                       not_after=dt.datetime.now(UTC) + dt.timedelta(days=90))


@pytest.fixture
def client(pg):
    repo = PostgresUptimeRepo(pg)
    overrides = {
        get_storage: lambda: PostgresStorage(pg),
        get_uptime_repo: lambda: repo,
        get_catalog_repo: lambda: PostgresCatalogRepo(pg),
        get_dns_resolver: lambda: _resolve,
        get_probe: lambda: _probe,
        get_app_settings: lambda: Settings.from_env(env={}),
        get_current_user: lambda: User("tester", "admin"),
    }
    app.dependency_overrides.update(overrides)
    yield TestClient(app), repo
    app.dependency_overrides.clear()


def _record(repo: PostgresUptimeRepo, monitor_id: int, up: bool, times: int = 1) -> None:
    for _ in range(times):
        repo.record_checks([CheckResult(
            monitor_id=monitor_id, checked_at=dt.datetime.now(UTC), is_up=up,
            status_code=200 if up else 503, response_ms=80,
            error=None if up else "Expected 200, got 503",
        )], evaluate)


def _setup_group(http: TestClient) -> tuple[dict, dict]:
    customer = http.post("/customers", json={"name": "Acme Corp", "contact_email": "ops@acme.com"})
    assert customer.status_code == 201, customer.text
    group = http.post("/groups", json={"customer_id": customer.json()["id"], "name": "E-commerce"})
    assert group.status_code == 201, group.text
    return customer.json(), group.json()


def test_group_one_click_add_and_detail(client):
    http, _repo = client
    customer, group = _setup_group(http)
    assert http.post("/customers", json={"name": "Acme Corp"}).status_code == 409

    r = http.post(f"/groups/{group['id']}/members", json={"target": "shop.acme.com"})
    assert r.status_code == 201, r.text
    assert r.json()["domain"]["customer_id"] == customer["id"]

    r = http.post(f"/groups/{group['id']}/members", json={"target": "shop.acme.com",
                                                           "add_website": False})
    assert r.json()["messages"] == [
        "shop.acme.com:443 was already monitored — moved into E-commerce"]

    blocked = http.post(f"/groups/{group['id']}/members", json={"target": "intranet.acme.com"})
    assert blocked.status_code == 422

    detail = http.get(f"/groups/{group['id']}").json()
    assert (detail["ssl_count"], detail["website_count"], detail["health"]) == (1, 1, "ok")
    assert detail["monitors"][0]["customer_name"] == "Acme Corp"
    assert [c["group_count"] for c in http.get("/customers").json()] == [1]
    assert len(http.get("/domains", params={"group_id": group["id"]}).json()) == 1


def test_monitor_state_rules_and_history(client):
    http, repo = client
    _customer, group = _setup_group(http)
    monitor = http.post("/monitors", json={"name": "Shop", "url": "https://shop.acme.com",
                                           "group_id": group["id"], "customer_id": None}).json()
    mid = monitor["id"]

    _record(repo, mid, up=True)
    _record(repo, mid, up=False, times=3)
    body = http.get(f"/monitors/{mid}").json()
    assert body["status"] == "down" and body["down_since"] is not None
    assert body["uptime_24h"] == 25.0
    assert http.get(f"/groups/{group['id']}").json()["health"] == "down"

    body = http.patch(f"/monitors/{mid}", json={"interval_seconds": 60}).json()
    assert body["status"] == "down"
    body = http.patch(f"/monitors/{mid}", json={"url": "https://shop.acme.com/v2"}).json()
    assert body["status"] == "unknown"
    [incident] = http.get(f"/monitors/{mid}/incidents").json()
    assert incident["close_reason"] == "edited"

    _record(repo, mid, up=False, times=3)
    assert http.post(f"/monitors/{mid}/pause").json()["status"] == "paused"
    reasons = [i["close_reason"] for i in http.get(f"/monitors/{mid}/incidents").json()]
    assert reasons == ["paused", "edited"]
    assert http.post(f"/monitors/{mid}/resume").json()["status"] == "unknown"

    assert len(http.get(f"/monitors/{mid}/checks", params={"limit": 5}).json()) == 5
    series = http.get(f"/monitors/{mid}/series").json()
    assert len(series) == 7 and series[0]["checked_at"] <= series[-1]["checked_at"]

    assert http.delete(f"/monitors/{mid}").status_code == 204
    assert http.get(f"/monitors/{mid}").status_code == 404


def test_deletes_ungroup_members(client):
    http, _repo = client
    customer, group = _setup_group(http)
    http.post(f"/groups/{group['id']}/members", json={"target": "shop.acme.com"})
    http.post("/domains", json={"domain": "blog.acme.com"})
    r = http.patch("/domains/blog.acme.com:443", json={"group_id": group["id"]})
    assert r.json()["customer_id"] == customer["id"]

    assert http.delete(f"/groups/{group['id']}").status_code == 204
    assert all(d["group_id"] is None for d in http.get("/domains").json())
    assert http.get("/monitors").json()[0]["group_id"] is None

    other = http.post("/groups", json={"customer_id": customer["id"], "name": "Blog"}).json()
    http.patch("/domains/blog.acme.com:443", json={"group_id": other["id"]})
    assert http.delete(f"/customers/{customer['id']}").status_code == 204
    assert http.get("/groups").json() == []
    blog = next(d for d in http.get("/domains").json() if d["domain"] == "blog.acme.com:443")
    assert blog["group_id"] is None
