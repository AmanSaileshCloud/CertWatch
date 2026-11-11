"""PostgresSettingsStore: the app_settings key/value table."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.storage.base import SettingsStorePort
from backend.ssl_monitor.adapters.storage.settings_postgres import PostgresSettingsStore

pytestmark = pytest.mark.postgres

T1 = dt.datetime(2026, 11, 11, 9, 0, tzinfo=dt.timezone.utc)
T2 = T1 + dt.timedelta(hours=1)


def test_satisfies_port(pg):
    assert isinstance(PostgresSettingsStore(pg), SettingsStorePort)


def test_empty_table(pg):
    stored = PostgresSettingsStore(pg).load()
    assert stored.values == {}
    assert stored.updated_at is None and stored.updated_by is None


def test_upsert_keeps_json_types_and_latest_audit(pg):
    store = PostgresSettingsStore(pg)
    store.save({
        "alerts.sender": "a@x.com",
        "alerts.default_recipients": ["noc@x.com", "ops@x.com"],
        "email.smtp_port": 587,
    }, updated_by="alice", now=T1)
    store.save({"alerts.sender": "b@x.com"}, updated_by="bob", now=T2)
    stored = store.load()
    assert stored.values == {
        "alerts.sender": "b@x.com",
        "alerts.default_recipients": ["noc@x.com", "ops@x.com"],
        "email.smtp_port": 587,
    }
    assert (stored.updated_at, stored.updated_by) == (T2, "bob")


def test_none_deletes_a_key(pg):
    store = PostgresSettingsStore(pg)
    store.save({"alerts.sender": "a@x.com", "alerts.reminder_minutes": 15},
               updated_by="alice", now=T1)
    store.save({"alerts.sender": None, "never.saved": None}, updated_by="bob", now=T2)
    stored = store.load()
    assert stored.values == {"alerts.reminder_minutes": 15}
    assert stored.updated_by == "alice"


def test_save_is_all_or_nothing(pg):
    store = PostgresSettingsStore(pg)
    store.save({"alerts.sender": "a@x.com"}, updated_by="alice", now=T1)
    with pytest.raises(Exception):
        store.save({"alerts.sender": None, "bad": object()}, updated_by="bob", now=T2)
    assert store.load().values == {"alerts.sender": "a@x.com"}
