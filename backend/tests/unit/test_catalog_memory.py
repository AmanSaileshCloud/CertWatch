"""MemoryCatalogRepo: customers and groups, same behaviour as the Postgres adapter."""

from __future__ import annotations

import pytest

from backend.ssl_monitor.adapters.storage.base import CatalogRepoPort, DuplicateNameError
from backend.ssl_monitor.adapters.storage.catalog_memory import MemoryCatalogRepo


def test_repo_satisfies_port():
    assert isinstance(MemoryCatalogRepo(), CatalogRepoPort)


def test_customer_crud_and_ordering():
    repo = MemoryCatalogRepo()
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
    assert repo.update_customer(99, {"name": "x"}) is None

    assert repo.delete_customer(globex.id) is True
    assert repo.delete_customer(globex.id) is False
    assert repo.list_customers() == [updated]


def test_update_customer_rejects_unknown_fields():
    repo = MemoryCatalogRepo()
    acme = repo.add_customer("Acme")
    with pytest.raises(ValueError, match="id"):
        repo.update_customer(acme.id, {"id": 5})


def test_duplicate_customer_names_are_rejected():
    repo = MemoryCatalogRepo()
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")

    with pytest.raises(DuplicateNameError):
        repo.add_customer("Acme")
    with pytest.raises(DuplicateNameError):
        repo.update_customer(globex.id, {"name": "Acme"})
    assert repo.update_customer(acme.id, {"name": "Acme"}).name == "Acme"  # own name is fine


def test_group_crud_and_ordering():
    repo = MemoryCatalogRepo()
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


def test_add_group_for_missing_customer_raises():
    with pytest.raises(ValueError):
        MemoryCatalogRepo().add_group(42, "Web")


def test_group_names_are_unique_per_customer():
    repo = MemoryCatalogRepo()
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")
    repo.add_group(acme.id, "Web")
    api = repo.add_group(acme.id, "API")

    assert repo.add_group(globex.id, "Web").name == "Web"
    with pytest.raises(DuplicateNameError):
        repo.add_group(acme.id, "Web")
    with pytest.raises(DuplicateNameError):
        repo.rename_group(api.id, "Web")
    assert repo.rename_group(api.id, "API").name == "API"


def test_delete_group_calls_hook_with_its_id():
    deleted: list[set[int]] = []
    repo = MemoryCatalogRepo(on_groups_deleted=deleted.append)
    acme = repo.add_customer("Acme")
    web = repo.add_group(acme.id, "Web")

    repo.delete_group(web.id)
    repo.delete_group(web.id)  # already gone: no second call

    assert deleted == [{web.id}]


def test_delete_customer_removes_its_groups_and_calls_hook():
    deleted: list[set[int]] = []
    repo = MemoryCatalogRepo(on_groups_deleted=deleted.append)
    acme = repo.add_customer("Acme")
    globex = repo.add_customer("Globex")
    web = repo.add_group(acme.id, "Web")
    api = repo.add_group(acme.id, "API")
    kept = repo.add_group(globex.id, "Web")

    repo.delete_customer(acme.id)

    assert deleted == [{web.id, api.id}]
    assert repo.list_groups() == [kept]


def test_delete_customer_without_groups_does_not_call_hook():
    deleted: list[set[int]] = []
    repo = MemoryCatalogRepo(on_groups_deleted=deleted.append)
    repo.delete_customer(repo.add_customer("Acme").id)
    assert deleted == []


def test_delete_customer_calls_customer_hook_after_groups_hook():
    events: list[tuple[str, object]] = []
    repo = MemoryCatalogRepo(
        on_groups_deleted=lambda ids: events.append(("groups", ids)),
        on_customer_deleted=lambda cid: events.append(("customer", cid)),
    )
    acme = repo.add_customer("Acme")
    web = repo.add_group(acme.id, "Web")

    repo.delete_customer(acme.id)
    repo.delete_customer(acme.id)  # already gone: no hooks

    assert events == [("groups", {web.id}), ("customer", acme.id)]
