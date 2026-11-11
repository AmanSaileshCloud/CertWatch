"""MemorySettingsStore: the in-memory SettingsStorePort."""

from __future__ import annotations

import datetime as dt

from backend.ssl_monitor.adapters.storage.base import SettingsStorePort
from backend.ssl_monitor.adapters.storage.settings_memory import MemorySettingsStore

T1 = dt.datetime(2026, 11, 11, 9, 0, tzinfo=dt.timezone.utc)
T2 = T1 + dt.timedelta(hours=1)


def test_satisfies_port():
    assert isinstance(MemorySettingsStore(), SettingsStorePort)


def test_empty_store_has_no_values_or_audit():
    stored = MemorySettingsStore().load()
    assert stored.values == {}
    assert stored.updated_at is None and stored.updated_by is None


def test_save_upserts_and_reports_the_latest_change():
    store = MemorySettingsStore()
    store.save({"alerts.sender": "a@x.com", "email.smtp_port": 587}, updated_by="alice", now=T1)
    store.save({"alerts.sender": "b@x.com"}, updated_by="bob", now=T2)
    stored = store.load()
    assert stored.values == {"alerts.sender": "b@x.com", "email.smtp_port": 587}
    assert (stored.updated_at, stored.updated_by) == (T2, "bob")


def test_none_deletes_a_key():
    store = MemorySettingsStore()
    store.save({"alerts.sender": "a@x.com", "alerts.reminder_minutes": 15},
               updated_by="alice", now=T1)
    store.save({"alerts.sender": None, "missing.key": None}, updated_by="bob", now=T2)
    assert store.load().values == {"alerts.reminder_minutes": 15}


def test_values_come_back_as_json_types():
    store = MemorySettingsStore()
    store.save({"alerts.default_recipients": ("a@x.com", "b@x.com")}, updated_by="a", now=T1)
    assert store.load().values["alerts.default_recipients"] == ["a@x.com", "b@x.com"]


def test_loaded_values_are_copies():
    store = MemorySettingsStore()
    store.save({"alerts.default_recipients": ["a@x.com"]}, updated_by="a", now=T1)
    store.load().values["alerts.default_recipients"].append("evil@x.com")
    assert store.load().values["alerts.default_recipients"] == ["a@x.com"]
