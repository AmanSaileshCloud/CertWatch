"""Per-domain notification config: update service, PATCH endpoint, checker gating."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import get_current_user, get_storage
from backend.ssl_monitor.auth.models import User
from backend.ssl_monitor.core.models import DomainRecord, ProbeResult, Status
from backend.ssl_monitor.services.checker import run_check
from backend.ssl_monitor.services.domains import (
    DomainNotFound,
    InvalidEmail,
    update_domain,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)
THRESHOLDS = [30, 14, 7, 1]


def _seed(store, host="ex.com", **kw):
    rec = DomainRecord.new(host=host, port=443, created_at=NOW)
    for k, v in kw.items():
        setattr(rec, k, v)
    store.put(rec)
    return rec


# ── update service ──────────────────────────────────────────────

def test_update_sets_emails_and_toggle():
    store = MemoryStorage()
    _seed(store)
    rec = update_domain(
        store, "ex.com:443", notify_emails=["A@x.com", "a@x.com", "b@y.com"], alerts_enabled=False
    )
    assert rec.notify_emails == ["a@x.com", "b@y.com"]  # lowercased + deduped
    assert rec.alerts_enabled is False
    assert store.get("ex.com:443").notify_emails == ["a@x.com", "b@y.com"]


def test_update_partial_leaves_other_field():
    store = MemoryStorage()
    _seed(store, notify_emails=["keep@x.com"])
    rec = update_domain(store, "ex.com:443", alerts_enabled=False)
    assert rec.notify_emails == ["keep@x.com"]  # untouched


def test_update_invalid_email_rejected():
    store = MemoryStorage()
    _seed(store)
    with pytest.raises(InvalidEmail):
        update_domain(store, "ex.com:443", notify_emails=["not-an-email"])


def test_update_missing_domain():
    with pytest.raises(DomainNotFound):
        update_domain(MemoryStorage(), "ghost:443", alerts_enabled=True)


# ── checker gating + recipients ─────────────────────────────────

def _probe(days):
    def fn(host, port, timeout):
        return ProbeResult(host, port, reachable=True, not_after=NOW + dt.timedelta(days=days))
    return fn


def test_alerts_disabled_suppresses_notification():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    _seed(store, alerts_enabled=False)
    summary = run_check(store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW, probe_fn=_probe(5))
    assert summary["alerts_sent"] == 0
    assert notifier.sent == []
    # dedup state stays None so re-enabling later fires
    assert store.get("ex.com:443").last_alert_threshold is None
    assert store.get("ex.com:443").status is Status.WARNING  # still checked


def test_enabled_alert_carries_domain_recipients():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    _seed(store, notify_emails=["ops@corp.test"])
    run_check(store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW, probe_fn=_probe(5))
    assert len(notifier.sent) == 1
    assert notifier.sent[0].recipients == ["ops@corp.test"]


# ── PATCH endpoint ──────────────────────────────────────────────

@pytest.fixture
def client():
    store = MemoryStorage()
    store.put(DomainRecord.new(host="ex.com", port=443, created_at=NOW))
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: User("tester", "admin")
    c = TestClient(app)
    yield c, store
    app.dependency_overrides.clear()


def test_patch_updates_config(client):
    c, store = client
    r = c.patch(
        "/domains/ex.com:443",
        json={"notify_emails": ["ops@corp.test"], "alerts_enabled": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["notify_emails"] == ["ops@corp.test"]
    assert body["alerts_enabled"] is False


def test_patch_unknown_domain_404(client):
    c, _ = client
    assert c.patch("/domains/ghost:443", json={"alerts_enabled": True}).status_code == 404


def test_patch_invalid_email_422(client):
    c, _ = client
    r = c.patch("/domains/ex.com:443", json={"notify_emails": ["nope"]})
    assert r.status_code == 422
