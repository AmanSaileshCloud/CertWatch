"""Monitor groups (Customer -> Group -> SSL domains + website monitors)."""

from __future__ import annotations

import datetime as dt

import pytest
from alembic import command
from sqlalchemy import delete, insert, inspect
from sqlalchemy.exc import IntegrityError

from backend.ssl_monitor.adapters.db.tables import customers, monitor_groups
from backend.ssl_monitor.adapters.storage.postgres import PostgresStorage
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
from backend.ssl_monitor.core.models import DomainRecord
from backend.ssl_monitor.core.uptime_models import Monitor

from .conftest import _alembic_config

pytestmark = pytest.mark.postgres

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)


def _customer(conn, name: str = "Acme") -> int:
    return conn.execute(insert(customers).values(name=name).returning(customers.c.id)).scalar_one()


def _group(conn, customer_id: int, name: str = "E-commerce") -> int:
    stmt = insert(monitor_groups).values(customer_id=customer_id, name=name)
    return conn.execute(stmt.returning(monitor_groups.c.id)).scalar_one()


def _members(pg, customer_id: int, group_id: int) -> tuple[str, int]:
    """Add one SSL domain and one website monitor to the group."""
    domain = DomainRecord.new(host="shop.acme.com", port=443, created_at=NOW)
    domain.customer_id, domain.group_id = customer_id, group_id
    PostgresStorage(pg).put(domain)
    monitor = PostgresUptimeRepo(pg).add_monitor(Monitor(
        id=0, name="Shop", url="https://shop.acme.com", interval_seconds=300,
        customer_id=customer_id, group_id=group_id))
    return domain.domain, monitor.id


def test_group_membership_round_trips_for_both_kinds(pg):
    with pg.begin() as conn:
        customer_id = _customer(conn)
        group_id = _group(conn, customer_id)
    domain_key, monitor_id = _members(pg, customer_id, group_id)

    assert PostgresStorage(pg).get(domain_key).group_id == group_id
    assert PostgresUptimeRepo(pg).get_monitor(monitor_id).group_id == group_id


def test_group_names_are_unique_per_customer(pg):
    with pg.begin() as conn:
        acme, globex = _customer(conn, "Acme"), _customer(conn, "Globex")
        _group(conn, acme, "Web")
        _group(conn, globex, "Web")
    with pytest.raises(IntegrityError), pg.begin() as conn:
        _group(conn, acme, "Web")


def test_deleting_a_group_only_ungroups_its_members(pg):
    with pg.begin() as conn:
        customer_id = _customer(conn)
        group_id = _group(conn, customer_id)
    domain_key, monitor_id = _members(pg, customer_id, group_id)

    with pg.begin() as conn:
        conn.execute(delete(monitor_groups).where(monitor_groups.c.id == group_id))

    domain = PostgresStorage(pg).get(domain_key)
    monitor = PostgresUptimeRepo(pg).get_monitor(monitor_id)
    assert (domain.group_id, monitor.group_id) == (None, None)
    assert (domain.customer_id, monitor.customer_id) == (customer_id, customer_id)


def test_deleting_a_customer_removes_its_groups_but_keeps_members(pg):
    with pg.begin() as conn:
        customer_id = _customer(conn)
        group_id = _group(conn, customer_id)
    domain_key, monitor_id = _members(pg, customer_id, group_id)

    with pg.begin() as conn:
        conn.execute(delete(customers).where(customers.c.id == customer_id))
        remaining = conn.execute(monitor_groups.select()).all()

    assert remaining == []
    assert PostgresStorage(pg).get(domain_key).group_id is None
    assert PostgresUptimeRepo(pg).get_monitor(monitor_id).group_id is None


def test_migration_0002_downgrades_and_reapplies_cleanly(pg, pg_url):
    config = _alembic_config(pg_url)
    command.downgrade(config, "0001")
    assert "monitor_groups" not in inspect(pg).get_table_names()
    command.upgrade(config, "head")
    assert "group_id" in {c["name"] for c in inspect(pg).get_columns("monitors")}
