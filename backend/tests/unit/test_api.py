"""API tests via FastAPI TestClient with in-memory adapters — no AWS, no network.

The probe is overridden with a fake so POST /checks/run is deterministic.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import (
    get_app_settings,
    get_current_user,
    get_notifier,
    get_probe,
    get_storage,
)
from backend.ssl_monitor.auth.models import User
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.models import ProbeResult

UTC = dt.timezone.utc


@pytest.fixture
def ctx():
    """Wire the app to in-memory adapters and a controllable fake probe."""
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    settings = Settings.from_env(env={})  # console notifier, default thresholds
    results: dict[str, ProbeResult] = {}

    def fake_probe(host: str, port: int, timeout: float) -> ProbeResult:
        if host in results:
            return results[host]
        # default: healthy, far-future expiry
        return ProbeResult(host, port, reachable=True,
                           not_after=dt.datetime.now(UTC) + dt.timedelta(days=90))

    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_notifier] = lambda: notifier
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_probe] = lambda: fake_probe
    app.dependency_overrides[get_current_user] = lambda: User("tester", "admin")

    client = TestClient(app)
    yield client, store, notifier, results
    app.dependency_overrides.clear()


def test_health(ctx):
    client, *_ = ctx
    assert client.get("/health").json() == {"status": "ok"}


def test_add_list_delete_domain(ctx):
    client, *_ = ctx

    r = client.post("/domains", json={"domain": "example.com"})
    assert r.status_code == 201
    body = r.json()
    assert body["domain"] == "example.com:443"
    assert body["status"] == "unreachable"  # not yet checked
    assert body["days_remaining"] is None

    r = client.get("/domains")
    assert [d["domain"] for d in r.json()] == ["example.com:443"]

    r = client.delete("/domains/example.com:443")
    assert r.status_code == 204
    assert client.get("/domains").json() == []


def test_add_custom_port(ctx):
    client, *_ = ctx
    r = client.post("/domains", json={"domain": "Example.COM", "port": 8443})
    assert r.status_code == 201
    assert r.json()["domain"] == "example.com:8443"  # lowercased + port in key


def test_duplicate_domain_conflicts(ctx):
    client, *_ = ctx
    client.post("/domains", json={"domain": "example.com"})
    r = client.post("/domains", json={"domain": "example.com"})
    assert r.status_code == 409


@pytest.mark.parametrize("bad", ["", "http://example.com", "example.com/path",
                                  "ex ample.com", "example.com:443"])
def test_invalid_hostname_rejected(ctx, bad):
    client, *_ = ctx
    r = client.post("/domains", json={"domain": bad})
    assert r.status_code == 422


@pytest.mark.parametrize("bad_port", [0, 70000, -1])
def test_invalid_port_rejected(ctx, bad_port):
    client, *_ = ctx
    r = client.post("/domains", json={"domain": "example.com", "port": bad_port})
    assert r.status_code == 422


def test_delete_missing_is_idempotent(ctx):
    client, *_ = ctx
    assert client.delete("/domains/nope:443").status_code == 204


def test_run_checks_updates_record_and_alerts(ctx):
    client, store, notifier, results = ctx
    client.post("/domains", json={"domain": "soon.test"})
    results["soon.test"] = ProbeResult(
        "soon.test", 443, reachable=True,
        not_after=dt.datetime.now(UTC) + dt.timedelta(days=6),
    )

    r = client.post("/checks/run")
    assert r.status_code == 200
    summary = r.json()
    assert summary["checked"] == 1
    assert summary["alerts_sent"] == 1
    assert len(notifier.sent) == 1
    assert notifier.sent[0].threshold == 7

    record = client.get("/domains").json()[0]
    assert record["status"] == "warning"
    assert record["last_alert_threshold"] == 7


def test_run_checks_unreachable_is_not_an_alert(ctx):
    client, store, notifier, results = ctx
    client.post("/domains", json={"domain": "down.test"})
    results["down.test"] = ProbeResult("down.test", 443, reachable=False,
                                        error="connection refused")

    summary = client.post("/checks/run").json()
    assert summary["alerts_sent"] == 0
    assert notifier.sent == []
    record = client.get("/domains").json()[0]
    assert record["status"] == "unreachable"
    assert record["last_error"] == "connection refused"
    assert record["days_remaining"] is None
