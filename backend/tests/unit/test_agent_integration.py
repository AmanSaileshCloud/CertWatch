"""Agent → server integration: provenance, no-clobber, and alerting.

The bug these guard against: the central checker used to probe agent-managed
hosts directly. From the server those private hosts are unreachable, so the
probe overwrote the agent's good data with ``unreachable`` / ``days=None`` —
and ``decide_alert`` never fires on ``None``. Net effect: an internal cert could
expire having never produced a single alert.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.adapters.storage.sqlite import SqliteStorage
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import get_app_settings, get_storage
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.models import (
    SOURCE_AGENT,
    SOURCE_DIRECT,
    DomainRecord,
    ProbeResult,
    Status,
)
from backend.ssl_monitor.services.checker import run_check

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
THRESHOLDS = [30, 14, 7, 1]
HOST, PORT = "ppitest.internal.tpml.in", 443
KEY = "ppitest.internal.tpml.in:443"
AGENT_KEY = "test-agent-key-abc123"


def _agent_row(store, *, days_left=None, not_after=None, last_report=NOW, agent="agent-001"):
    """Seed an agent-managed record the way receive_report would."""
    rec = DomainRecord.new(host=HOST, port=PORT, created_at=NOW)
    rec.source = SOURCE_AGENT
    rec.agent_id = agent
    if days_left is not None:
        not_after = NOW + dt.timedelta(days=days_left)
    rec.not_after = not_after
    rec.days_remaining = days_left
    rec.status = Status.WARNING if days_left is not None and days_left <= 30 else Status.OK
    rec.last_checked_at = last_report
    store.put(rec)
    return rec


def _probe_fails(host, port, timeout):
    """What the central checker sees for a private host: nothing."""
    return ProbeResult(host, port, reachable=False, error="timeout: internal host")


def _run(store, notifier, *, now=NOW, stale_hours=6.0):
    return run_check(
        store, notifier,
        thresholds=THRESHOLDS, timeout=5, now=now,
        probe_fn=_probe_fails, agent_stale_after_hours=stale_hours,
    )


# ── the regression this whole change exists for ─────────────────


def test_agent_reported_expiring_cert_actually_alerts():
    """An internal cert nearing expiry must page, even though the server
    cannot probe it. This is the case that silently failed before."""
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=5)

    _run(store, notifier)

    assert len(notifier.sent) == 1, "expiring agent-managed cert must alert"
    alert = notifier.sent[0]
    assert alert.domain == KEY
    assert alert.threshold == 7
    assert alert.days_remaining == 5


def test_checker_never_probes_an_agent_managed_host():
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=5)

    probed: list[str] = []

    def spy(host, port, timeout):
        probed.append(host)
        return ProbeResult(host, port, reachable=False, error="unreachable")

    summary = run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
        probe_fn=spy, agent_stale_after_hours=6.0,
    )

    assert probed == [], "agent-managed hosts must not be probed from the server"
    assert summary["probed"] == 0
    assert summary["checked"] == 1


def test_agent_data_survives_a_checker_pass():
    """The clobber bug: status/days must not be reset to unreachable/None."""
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=45)

    _run(store, notifier)

    rec = store.get(KEY)
    assert rec.status is Status.OK
    assert rec.days_remaining == 45
    assert rec.not_after is not None


def test_countdown_keeps_running_between_agent_reports():
    """days_remaining is re-derived from stored notAfter, so the cert still
    crosses thresholds even if the agent hasn't reported since."""
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=45)  # outside every threshold today

    _run(store, notifier)
    assert notifier.sent == []

    # 20 days later the agent is silent, but the cert now has 25 days left —
    # it has crossed the 30-day threshold and must page off stored data alone.
    _run(store, notifier, now=NOW + dt.timedelta(days=20), stale_hours=24 * 365)
    assert len(notifier.sent) == 1
    assert notifier.sent[0].threshold == 30
    assert notifier.sent[0].days_remaining == 25


def test_direct_domains_are_still_probed():
    """Regression guard: the agent branch must not change direct behaviour."""
    store, notifier = MemoryStorage(), ConsoleNotifier()
    store.put(DomainRecord.new(host="example.com", port=443, created_at=NOW))

    summary = _run(store, notifier)

    rec = store.get("example.com:443")
    assert rec.source == SOURCE_DIRECT
    assert rec.status is Status.UNREACHABLE  # probe failed, as configured
    assert summary["probed"] == 1


# ── stale-agent detection ───────────────────────────────────────


def test_silent_agent_alerts_once_then_dedupes():
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=200, last_report=NOW - dt.timedelta(hours=12))

    summary = _run(store, notifier)
    assert summary["stale_agents"] == 1
    assert len(notifier.sent) == 1
    assert "has not reported" in notifier.sent[0].detail

    # Next pass: still stale, but must not re-page.
    _run(store, notifier, now=NOW + dt.timedelta(minutes=5))
    assert len(notifier.sent) == 1


def test_fresh_report_clears_the_stale_flag():
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=200, last_report=NOW - dt.timedelta(hours=12))
    _run(store, notifier)
    assert store.get(KEY).stale_alert_sent is True

    # Agent comes back.
    _agent_row(store, days_left=200, last_report=NOW)
    _run(store, notifier)
    assert store.get(KEY).stale_alert_sent is False


def test_stale_agent_records_a_readable_error():
    store, notifier = MemoryStorage(), ConsoleNotifier()
    _agent_row(store, days_left=200, last_report=NOW - dt.timedelta(hours=30))
    _run(store, notifier)
    assert "has not reported" in store.get(KEY).last_error


def test_muted_domain_does_not_stale_alert():
    store, notifier = MemoryStorage(), ConsoleNotifier()
    rec = _agent_row(store, days_left=5, last_report=NOW - dt.timedelta(hours=12))
    rec.alerts_enabled = False
    store.put(rec)

    _run(store, notifier)
    assert notifier.sent == []


# ── the endpoint: server-side classification ────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", AGENT_KEY)
    store = MemoryStorage()
    settings = Settings.from_env(env={"THRESHOLD_DAYS": "30,14,7,1"})
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_app_settings] = lambda: settings
    yield TestClient(app), store
    app.dependency_overrides.clear()


def _report(client, **over):
    body = {
        "agent_id": "agent-001",
        "domain": HOST,
        "port": PORT,
        "reachable": True,
        "private": True,
        "not_after": (NOW + dt.timedelta(days=90)).isoformat(),
    }
    body.update(over)
    return client.post("/agent/report", json=body, headers={"X-Agent-Key": AGENT_KEY})


@pytest.mark.parametrize(
    "remaining, expected",
    [
        (dt.timedelta(days=90), "ok"),
        (dt.timedelta(days=20), "warning"),
        # Under a day left is still a warning, not yet expired.
        (dt.timedelta(hours=12), "warning"),
        (dt.timedelta(days=-3), "expired"),
    ],
)
def test_server_classifies_reported_expiry(client, remaining, expected):
    c, store = client
    r = _report(c, not_after=(dt.datetime.now(UTC) + remaining).isoformat())
    assert r.status_code == 200
    assert r.json()["status"] == expected
    assert store.get(KEY).status.value == expected


def test_healthy_cert_is_not_recorded_as_unreachable(client):
    """The old agent sent status="healthy", which is not in the Status enum, so
    the server mapped it to UNREACHABLE — flagging good certs as broken."""
    c, store = client
    r = _report(c, status="healthy")  # legacy field must be ignored, not trusted
    assert r.json()["status"] == "ok"
    assert store.get(KEY).status is Status.OK


def test_report_marks_the_record_agent_managed(client):
    c, store = client
    _report(c)
    rec = store.get(KEY)
    assert rec.source == SOURCE_AGENT
    assert rec.agent_id == "agent-001"
    assert rec.is_agent_managed


def test_unreachable_report_is_never_an_expiry(client):
    c, store = client
    r = _report(c, reachable=False, not_after=None, error="connection refused")
    assert r.json()["status"] == "unreachable"
    rec = store.get(KEY)
    assert rec.days_remaining is None
    assert rec.not_after is None
    assert "connection refused" in rec.last_error


def test_legacy_certificate_expiry_field_still_accepted(client):
    c, store = client
    r = _report(
        c, not_after=None,
        certificate_expiry=(dt.datetime.now(UTC) + dt.timedelta(days=60)).isoformat(),
    )
    assert r.json()["status"] == "ok"
    assert store.get(KEY).not_after is not None


def test_bad_key_rejected(client):
    c, _ = client
    r = c.post("/agent/report", json={
        "agent_id": "x", "domain": HOST, "reachable": True,
    }, headers={"X-Agent-Key": "wrong"})
    assert r.status_code == 401


def test_missing_key_rejected(client):
    c, _ = client
    r = c.post("/agent/report", json={
        "agent_id": "x", "domain": HOST, "reachable": True,
    })
    assert r.status_code == 401


def test_unconfigured_server_key_is_503(monkeypatch):
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    app.dependency_overrides[get_storage] = lambda: MemoryStorage()
    try:
        c = TestClient(app)
        r = c.post("/agent/report", json={
            "agent_id": "x", "domain": HOST, "reachable": True,
        }, headers={"X-Agent-Key": "anything"})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


# ── end-to-end: report then check ───────────────────────────────


def test_report_then_checker_pass_alerts_and_keeps_data(client):
    """The full path that used to lose the alert."""
    c, store = client
    _report(c, not_after=(dt.datetime.now(UTC) + dt.timedelta(days=5)).isoformat())
    assert store.get(KEY).status is Status.WARNING

    notifier = ConsoleNotifier()
    run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5,
        now=dt.datetime.now(UTC), probe_fn=_probe_fails, agent_stale_after_hours=6.0,
    )

    assert len(notifier.sent) == 1
    assert store.get(KEY).status is Status.WARNING  # not clobbered


# ── storage migration ───────────────────────────────────────────


def test_sqlite_migrates_a_pre_agent_database(tmp_path):
    """A deployed certwatch.db predates these columns. CREATE TABLE IF NOT
    EXISTS is a no-op on it, so the ALTERs must run or agent rows read back as
    direct — resurrecting the overwrite bug on upgrade."""
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE domains (
                domain TEXT PRIMARY KEY, host TEXT NOT NULL, port INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, not_after TEXT,
                days_remaining INTEGER, last_checked_at TEXT, last_error TEXT,
                last_alert_threshold INTEGER,
                alerts_enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO domains (domain, host, port, status, created_at, alerts_enabled)
            VALUES ('legacy.com:443','legacy.com',443,'ok','2026-01-01T00:00:00+00:00',1);
        """)

    store = SqliteStorage(str(db))          # must migrate, not crash
    legacy = store.get("legacy.com:443")
    assert legacy.source == SOURCE_DIRECT   # existing rows default to direct
    assert legacy.agent_id is None
    assert legacy.stale_alert_sent is False

    rec = _agent_row(store, days_left=10)
    assert store.get(rec.domain).is_agent_managed

    SqliteStorage(str(db))                  # re-open: migration is idempotent
