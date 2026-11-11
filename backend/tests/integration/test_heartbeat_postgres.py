"""Worker heartbeats against a real PostgreSQL."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.storage.heartbeat_postgres import PostgresHeartbeats

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def test_last_beat_is_none_until_the_worker_reports(pg) -> None:
    assert PostgresHeartbeats(pg).last_beat("uptime") is None


def test_a_new_beat_replaces_the_previous_one(pg) -> None:
    beats = PostgresHeartbeats(pg)
    beats.beat("uptime", NOW)
    beats.beat("uptime", NOW + dt.timedelta(seconds=10))
    assert beats.last_beat("uptime") == NOW + dt.timedelta(seconds=10)
    assert beats.last_beat("other") is None


def test_ping_succeeds_against_a_live_database(pg) -> None:
    PostgresHeartbeats(pg).ping()
