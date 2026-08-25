"""Unit tests for the in-memory storage adapter and StoragePort conformance."""

from __future__ import annotations

import datetime as dt

from backend.ssl_monitor.adapters.storage.base import StoragePort
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.core.models import DomainRecord, Status

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


def _record(host="example.com", port=443) -> DomainRecord:
    return DomainRecord.new(host=host, port=port, created_at=NOW)


def test_memory_storage_satisfies_port():
    assert isinstance(MemoryStorage(), StoragePort)


def test_put_get_list_delete_roundtrip():
    store = MemoryStorage()
    rec = _record()
    store.put(rec)

    got = store.get(rec.domain)
    assert got is not None and got.domain == "example.com:443"
    assert [r.domain for r in store.list()] == ["example.com:443"]

    store.delete(rec.domain)
    assert store.get(rec.domain) is None
    assert store.list() == []


def test_get_missing_returns_none():
    assert MemoryStorage().get("nope:443") is None


def test_delete_missing_is_noop():
    MemoryStorage().delete("nope:443")  # must not raise


def test_stored_copy_is_isolated_from_caller_mutation():
    store = MemoryStorage()
    rec = _record()
    store.put(rec)
    rec.status = Status.EXPIRED  # mutate caller's copy after storing
    assert store.get(rec.domain).status is Status.UNREACHABLE
