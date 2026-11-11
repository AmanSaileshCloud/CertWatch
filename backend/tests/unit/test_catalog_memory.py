"""MemoryCatalogRepo: customers and groups, same behaviour as the Postgres adapter."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.ssl_monitor.adapters.storage.base import CatalogRepoPort, DuplicateNameError
from backend.ssl_monitor.adapters.storage.catalog_memory import MemoryCatalogRepo
from backend.ssl_monitor.core.alert_models import AlertKind, AlertRule
from backend.ssl_monitor.core.uptime_models import AlertChannel, Monitor


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


# --- Step 4: alert rules ---------------------------------------------------------------


def _alert_setup():
    """A catalog repo, customer Acme, and a website monitor that belongs to Acme
    (monitors are not stored in the catalog, so the monitor is only a value)."""
    repo = MemoryCatalogRepo()
    acme = repo.add_customer("Acme")
    site = Monitor(id=1, name="Site", url="https://acme.com", interval_seconds=300,
                   customer_id=acme.id)
    return repo, acme, site

def _rule(target: str, **fields) -> AlertRule:
    return AlertRule(id=0, target=target, **fields)


def test_alert_rule_crud_and_scopes():
    repo, acme, site = _alert_setup()
    global_b = repo.add_alert_rule(_rule("b@x.com"))
    global_a = repo.add_alert_rule(_rule("a@x.com", on_recovery=False))
    for_acme = repo.add_alert_rule(_rule("ops@acme.com", customer_id=acme.id))
    for_site = repo.add_alert_rule(_rule("dev@acme.com", monitor_id=site.id))

    assert (global_a.channel, global_a.on_down, global_a.on_recovery, global_a.enabled) == (
        AlertChannel.EMAIL, True, False, True)
    assert global_a.created_at is not None and global_a.id != global_b.id
    assert repo.list_alert_rules() == [global_a, global_b]
    assert repo.list_alert_rules(customer_id=acme.id) == [for_acme]
    assert repo.list_alert_rules(monitor_id=site.id) == [for_site]
    assert repo.list_alert_rules(customer_id=999) == []

    updated = repo.update_alert_rule(for_acme.id, {"on_down": False, "enabled": False})
    assert updated == replace(for_acme, on_down=False, enabled=False)
    assert repo.list_alert_rules(customer_id=acme.id) == [updated]
    assert repo.update_alert_rule(for_acme.id, {}) == updated
    assert repo.update_alert_rule(999, {"enabled": True}) is None

    assert repo.delete_alert_rule(global_b.id) is True
    assert repo.delete_alert_rule(global_b.id) is False
    assert repo.list_alert_rules() == [global_a]


def test_alert_rule_target_is_lower_cased():
    repo, _, _ = _alert_setup()
    assert repo.add_alert_rule(_rule(" Ops@Acme.COM ")).target == "ops@acme.com"


def test_update_alert_rule_rejects_unknown_fields():
    repo, _, _ = _alert_setup()
    rule = repo.add_alert_rule(_rule("a@x.com"))
    with pytest.raises(ValueError, match="target"):
        repo.update_alert_rule(rule.id, {"target": "b@x.com"})


def test_duplicate_alert_rule_targets_are_rejected_per_scope():
    repo, acme, site = _alert_setup()
    for scope in ({}, {"customer_id": acme.id}, {"monitor_id": site.id}):
        repo.add_alert_rule(_rule("ops@acme.com", **scope))
        with pytest.raises(DuplicateNameError):
            repo.add_alert_rule(_rule("OPS@acme.com", **scope))

    assert len(repo.list_alert_rules(customer_id=acme.id)) == 1


def test_alert_rule_for_customer_and_monitor_is_rejected():
    repo, acme, site = _alert_setup()
    with pytest.raises(ValueError) as caught:
        repo.add_alert_rule(_rule("a@x.com", customer_id=acme.id, monitor_id=site.id))
    assert not isinstance(caught.value, DuplicateNameError)
    assert repo.list_alert_rules(customer_id=acme.id) == []


def test_alert_rule_for_missing_customer_raises_value_error():
    repo, _, _ = _alert_setup()
    with pytest.raises(ValueError) as caught:
        repo.add_alert_rule(_rule("a@x.com", customer_id=999))
    assert not isinstance(caught.value, DuplicateNameError)


def test_deleting_a_customer_deletes_its_alert_rules():
    repo, acme, _ = _alert_setup()
    repo.add_alert_rule(_rule("ops@acme.com", customer_id=acme.id))
    repo.delete_customer(acme.id)
    assert repo.list_alert_rules(customer_id=acme.id) == []


def test_recipients_combine_global_customer_and_monitor_rules():
    repo, acme, site = _alert_setup()
    globex = repo.add_customer("Globex")
    repo.add_alert_rule(_rule("global@x.com"))
    repo.add_alert_rule(_rule("ops@acme.com", customer_id=acme.id))
    repo.add_alert_rule(_rule("dev@acme.com", monitor_id=site.id))
    repo.add_alert_rule(_rule("ops@globex.com", customer_id=globex.id))
    repo.add_alert_rule(_rule("other@acme.com", monitor_id=site.id + 1))

    assert repo.recipients_for(site, AlertKind.DOWN) == [
        "dev@acme.com", "global@x.com", "ops@acme.com"]
    loose = replace(site, id=site.id + 1, customer_id=None)
    assert repo.recipients_for(loose, AlertKind.DOWN) == ["global@x.com", "other@acme.com"]


def test_recipients_filter_by_kind_and_enabled():
    repo, acme, site = _alert_setup()
    repo.add_alert_rule(_rule("both@x.com"))
    repo.add_alert_rule(_rule("down@x.com", on_recovery=False))
    repo.add_alert_rule(_rule("up@x.com", customer_id=acme.id, on_down=False))
    repo.add_alert_rule(_rule("neither@x.com", monitor_id=site.id, on_down=False,
                              on_recovery=False))
    repo.add_alert_rule(_rule("off@x.com", enabled=False))

    assert repo.recipients_for(site, AlertKind.DOWN) == ["both@x.com", "down@x.com"]
    assert repo.recipients_for(site, AlertKind.REMINDER) == ["both@x.com", "down@x.com"]
    assert repo.recipients_for(site, AlertKind.RECOVERED) == ["both@x.com", "up@x.com"]
    assert repo.recipients_for(site, AlertKind.TEST) == [
        "both@x.com", "down@x.com", "neither@x.com", "up@x.com"]


def test_recipients_are_de_duplicated():
    repo, acme, site = _alert_setup()
    repo.add_alert_rule(_rule("ops@acme.com"))
    repo.add_alert_rule(_rule("OPS@acme.com", customer_id=acme.id))
    repo.add_alert_rule(_rule("Ops@Acme.com", monitor_id=site.id))

    assert repo.recipients_for(site, AlertKind.DOWN) == ["ops@acme.com"]
