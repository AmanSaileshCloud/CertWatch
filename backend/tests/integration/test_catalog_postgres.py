"""PostgresCatalogRepo: customers and monitor groups."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.storage.base import CatalogRepoPort, DuplicateNameError
from backend.ssl_monitor.adapters.storage.catalog_postgres import PostgresCatalogRepo
from backend.ssl_monitor.adapters.storage.postgres import PostgresStorage
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
from backend.ssl_monitor.core.models import DomainRecord
from backend.ssl_monitor.core.uptime_models import Monitor

pytestmark = pytest.mark.postgres

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)


def _members(pg, customer_id: int, group_id: int) -> tuple[str, int]:
    """Add one SSL domain and one website monitor to the group."""
    domain = DomainRecord.new(host="shop.acme.com", port=443, created_at=NOW)
    domain.customer_id, domain.group_id = customer_id, group_id
    PostgresStorage(pg).put(domain)
    monitor = PostgresUptimeRepo(pg).add_monitor(Monitor(
        id=0, name="Shop", url="https://shop.acme.com", interval_seconds=300,
        customer_id=customer_id, group_id=group_id))
    return domain.domain, monitor.id


def _group_ids(pg, domain_key: str, monitor_id: int) -> tuple[int | None, int | None]:
    return (PostgresStorage(pg).get(domain_key).group_id,
            PostgresUptimeRepo(pg).get_monitor(monitor_id).group_id)


def test_repo_satisfies_port(pg):
    assert isinstance(PostgresCatalogRepo(pg), CatalogRepoPort)


def test_customer_crud_and_ordering(pg):
    repo = PostgresCatalogRepo(pg)
    globex = repo.add_customer("Globex")
    acme = repo.add_customer("Acme", "ops@acme.com")

    assert (acme.id, acme.contact_email) == (2, "ops@acme.com")
    assert acme.created_at is not None
    assert [c.name for c in repo.list_customers()] == ["Acme", "Globex"]
    assert repo.get_customer(globex.id) == globex
    assert repo.get_customer(99) is None

    updated = repo.update_customer(acme.id, {"name": "Acme Corp", "contact_email": None})
    assert (updated.name, updated.contact_email) == ("Acme Corp", None)
    assert repo.get_customer(acme.id) == updated
    assert repo.update_customer(acme.id, {}) == updated
    assert repo.update_customer(99, {"name": "x"}) is None

    assert repo.delete_customer(globex.id) is True
    assert repo.delete_customer(globex.id) is False
    assert repo.list_customers() == [updated]


def test_update_customer_rejects_unknown_fields(pg):
    repo = PostgresCatalogRepo(pg)
    acme = repo.add_customer("Acme")
    with pytest.raises(ValueError, match="id"):
        repo.update_customer(acme.id, {"id": 5})


def test_duplicate_customer_names_are_rejected(pg):
    repo = PostgresCatalogRepo(pg)
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")

    with pytest.raises(DuplicateNameError):
        repo.add_customer("Acme")
    with pytest.raises(DuplicateNameError):
        repo.update_customer(globex.id, {"name": "Acme"})
    assert repo.update_customer(acme.id, {"name": "Acme"}).name == "Acme"


def test_group_crud_and_ordering(pg):
    repo = PostgresCatalogRepo(pg)
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")
    web = repo.add_group(acme.id, "Web")
    api = repo.add_group(acme.id, "API")
    other = repo.add_group(globex.id, "Billing")

    assert web.customer_id == acme.id and web.created_at is not None
    assert [g.name for g in repo.list_groups()] == ["API", "Billing", "Web"]
    assert repo.list_groups(acme.id) == [api, web]
    assert repo.get_group(other.id) == other
    assert repo.get_group(99) is None

    renamed = repo.rename_group(web.id, "Website")
    assert renamed.name == "Website" and repo.get_group(web.id) == renamed
    assert repo.rename_group(99, "x") is None

    assert repo.delete_group(api.id) is True
    assert repo.delete_group(api.id) is False
    assert repo.list_groups(acme.id) == [renamed]


def test_add_group_for_missing_customer_raises_value_error(pg):
    with pytest.raises(ValueError) as caught:
        PostgresCatalogRepo(pg).add_group(42, "Web")
    assert not isinstance(caught.value, DuplicateNameError)


def test_group_names_are_unique_per_customer(pg):
    repo = PostgresCatalogRepo(pg)
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")
    repo.add_group(acme.id, "Web")
    api = repo.add_group(acme.id, "API")

    assert repo.add_group(globex.id, "Web").name == "Web"
    with pytest.raises(DuplicateNameError):
        repo.add_group(acme.id, "Web")
    with pytest.raises(DuplicateNameError):
        repo.rename_group(api.id, "Web")
    assert [g.name for g in repo.list_groups(acme.id)] == ["API", "Web"]


def test_delete_group_ungroups_its_members(pg):
    repo = PostgresCatalogRepo(pg)
    acme = repo.add_customer("Acme")
    web = repo.add_group(acme.id, "Web")
    domain_key, monitor_id = _members(pg, acme.id, web.id)

    repo.delete_group(web.id)

    assert _group_ids(pg, domain_key, monitor_id) == (None, None)
    assert PostgresUptimeRepo(pg).get_monitor(monitor_id).customer_id == acme.id


def test_delete_customer_removes_its_groups_and_ungroups_members(pg):
    repo = PostgresCatalogRepo(pg)
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")
    web = repo.add_group(acme.id, "Web")
    kept = repo.add_group(globex.id, "Web")
    domain_key, monitor_id = _members(pg, acme.id, web.id)

    repo.delete_customer(acme.id)

    assert repo.list_groups() == [kept]
    assert _group_ids(pg, domain_key, monitor_id) == (None, None)
