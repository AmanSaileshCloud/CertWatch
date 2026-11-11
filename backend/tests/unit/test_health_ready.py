"""/health/ready and the heartbeat rules behind it."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import get_heartbeats
from backend.ssl_monitor.core.health import UPTIME_WORKER, heartbeat_problem

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def test_recent_heartbeat_is_fine() -> None:
    assert heartbeat_problem(NOW - dt.timedelta(seconds=30), NOW) is None


def test_stale_heartbeat_is_a_problem() -> None:
    assert "600 s ago" in heartbeat_problem(NOW - dt.timedelta(minutes=10), NOW)


def test_missing_heartbeat_is_a_problem() -> None:
    assert "never" in heartbeat_problem(None, NOW)


class FakeHeartbeats:
    def __init__(self, last_beat=None, *, db_down: bool = False) -> None:
        self.last, self.db_down = last_beat, db_down
        self.asked: list[str] = []

    def ping(self) -> None:
        if self.db_down:
            raise ConnectionError("connection refused")

    def last_beat(self, name: str):
        self.asked.append(name)
        return self.last


@pytest.fixture
def ready():
    def call(heartbeats):
        app.dependency_overrides[get_heartbeats] = lambda: heartbeats
        try:
            return TestClient(app).get("/health/ready")
        finally:
            app.dependency_overrides.pop(get_heartbeats, None)
    return call


def test_ready_when_database_answers_and_worker_is_beating(ready) -> None:
    beats = FakeHeartbeats(dt.datetime.now(UTC) - dt.timedelta(seconds=5))
    r = ready(beats)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert beats.asked == [UPTIME_WORKER]


def test_not_ready_when_the_worker_has_stopped(ready) -> None:
    r = ready(FakeHeartbeats(dt.datetime.now(UTC) - dt.timedelta(minutes=10)))
    assert r.status_code == 503
    assert "uptime worker" in r.json()["problem"]


def test_not_ready_when_the_database_is_down(ready) -> None:
    r = ready(FakeHeartbeats(db_down=True))
    assert r.status_code == 503
    assert r.json()["problem"] == "the database is unreachable"


def test_ready_without_postgres_storage(ready) -> None:
    r = ready(None)
    assert r.status_code == 200
    assert r.json()["database"] == "not used"


def test_ready_needs_no_login(ready) -> None:
    # The external alarm (Route 53 health check) has no token.
    r = ready(FakeHeartbeats(dt.datetime.now(UTC)))
    assert r.status_code == 200
