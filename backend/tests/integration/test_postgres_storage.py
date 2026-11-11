"""PostgresStorage (SSL domains) and the SQLite → Postgres data migration."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import insert

from backend.scripts.migrate_sqlite_to_postgres import migrate_domains
from backend.ssl_monitor.adapters.db.tables import customers
from backend.ssl_monitor.adapters.storage.postgres import PostgresStorage
from backend.ssl_monitor.adapters.storage.sqlite import SqliteStorage
from backend.ssl_monitor.core.models import SOURCE_AGENT, DomainRecord, Status

pytestmark = pytest.mark.postgres

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _record(host: str = "example.com", **overrides) -> DomainRecord:
    record = DomainRecord.new(host=host, port=443, created_at=NOW)
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


def test_round_trip_keeps_every_field(pg):
    storage = PostgresStorage(pg)
    record = _record(
        status=Status.WARNING,
        not_after=NOW + dt.timedelta(days=6),
        days_remaining=6,
        last_checked_at=NOW,
        last_alert_threshold=7,
        alerts_enabled=False,
        source=SOURCE_AGENT,
        agent_id="i-0abc",
        stale_alert_sent=True,
    )
    storage.put(record)
    assert storage.get(record.domain) == record


def test_put_overwrites_existing_row(pg):
    storage = PostgresStorage(pg)
    record = _record()
    storage.put(record)
    record.status = Status.OK
    storage.put(record)
    assert storage.get(record.domain).status is Status.OK
    assert len(storage.list()) == 1


def test_customer_link_is_persisted(pg):
    with pg.begin() as conn:
        conn.execute(insert(customers).values(id=7, name="Acme"))
    storage = PostgresStorage(pg)
    storage.put(_record(customer_id=7))
    assert storage.get("example.com:443").customer_id == 7


def test_delete_is_idempotent(pg):
    storage = PostgresStorage(pg)
    storage.put(_record())
    storage.delete("example.com:443")
    storage.delete("example.com:443")
    assert storage.get("example.com:443") is None


def _sqlite_with(tmp_path, *hosts: str) -> SqliteStorage:
    source = SqliteStorage(str(tmp_path / "legacy.db"))
    for host in hosts:
        source.put(_record(host, status=Status.OK, days_remaining=40))
    return source


def test_migration_copies_all_domains(pg, tmp_path):
    source = _sqlite_with(tmp_path, "a.example.com", "b.example.com")
    target = PostgresStorage(pg)

    report = migrate_domains(source, target)

    assert (report.found, report.copied, report.skipped) == (2, 2, 0)
    assert {r.domain for r in target.list()} == {r.domain for r in source.list()}


def test_migration_rerun_never_overwrites_newer_data(pg, tmp_path):
    source = _sqlite_with(tmp_path, "a.example.com")
    target = PostgresStorage(pg)
    migrate_domains(source, target)
    newer = target.get("a.example.com:443")
    newer.status = Status.EXPIRED
    target.put(newer)

    report = migrate_domains(source, target)

    assert (report.copied, report.skipped) == (0, 1)
    assert target.get("a.example.com:443").status is Status.EXPIRED


def test_migration_dry_run_writes_nothing(pg, tmp_path):
    source = _sqlite_with(tmp_path, "a.example.com", "b.example.com")
    target = PostgresStorage(pg)

    report = migrate_domains(source, target, dry_run=True)

    assert report.copied == 2
    assert target.list() == []
