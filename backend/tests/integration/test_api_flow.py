"""Full flow against real SQLite storage:

    add a domain (API) -> run the checker (API) -> assert the persisted SQLite
    record AND that exactly one console notification fired -> a second run must
    not re-alert (idempotency through persisted state).

The probe is faked so the assertion is deterministic (we can't control a real
cert's expiry); the notifier is a shared ConsoleNotifier so we can count what it
emitted. Uses a temp SQLite file — no external services, always runs.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.sqlite import SqliteStorage
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


def test_full_add_run_assert_flow(tmp_path):
    db = str(tmp_path / "flow.db")
    storage = SqliteStorage(db)
    notifier = ConsoleNotifier()
    settings = Settings.from_env(
        env={"STORAGE": "sqlite", "SQLITE_PATH": db, "NOTIFIER": "console", "AUTH_ENABLED": "false"}
    )

    def fake_probe(host: str, port: int, timeout: float) -> ProbeResult:
        # expiring in ~6 days → crosses the 7-day threshold
        return ProbeResult(host, port, reachable=True,
                           not_after=dt.datetime.now(UTC) + dt.timedelta(days=6))

    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_notifier] = lambda: notifier
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_probe] = lambda: fake_probe
    app.dependency_overrides[get_current_user] = lambda: User("tester", "admin")

    try:
        client = TestClient(app)

        # 1. add a domain — persisted to SQLite
        r = client.post("/domains", json={"domain": "example.com"})
        assert r.status_code == 201

        # 2. run the checker
        summary = client.post("/checks/run").json()
        assert summary["checked"] == 1
        assert summary["alerts_sent"] == 1

        # 3a. assert the persisted record (read straight from SQLite)
        stored = storage.get("example.com:443")
        assert stored is not None
        assert stored.status.value == "warning"
        assert stored.last_alert_threshold == 7
        assert stored.not_after is not None

        # 3b. assert exactly one console notification fired
        assert len(notifier.sent) == 1
        assert notifier.sent[0].domain == "example.com:443"
        assert notifier.sent[0].threshold == 7

        # 4. a second run must NOT re-alert (idempotency through persisted state)
        client.post("/checks/run")
        assert len(notifier.sent) == 1
    finally:
        app.dependency_overrides.clear()
