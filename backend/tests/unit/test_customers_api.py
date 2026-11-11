"""/customers endpoints against in-memory repos (see conftest ``api`` fixture)."""

from __future__ import annotations

import pytest


def test_create_customer(api):
    r = api.client.post("/customers", json={"name": "  Acme Corp ", "contact_email": "ops@acme.com"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Acme Corp"
    assert body["contact_email"] == "ops@acme.com"
    assert body["group_count"] == 0
    assert body["created_at"] is not None


def test_list_customers_by_name_with_group_count(api):
    zed = api.customer("Zed Ltd")
    api.customer("Acme Corp")
    api.group(zed["id"], "One")
    api.group(zed["id"], "Two")
    body = api.client.get("/customers").json()
    assert [(c["name"], c["group_count"]) for c in body] == [("Acme Corp", 0), ("Zed Ltd", 2)]


@pytest.mark.parametrize("payload, message", [
    ({"name": "   "}, "must not be blank"),
    ({"name": "x" * 201}, "at most 200"),
    ({"name": "Acme", "contact_email": "not-an-email"}, "invalid contact email"),
])
def test_create_validation(api, payload, message):
    r = api.client.post("/customers", json=payload)
    assert r.status_code == 422
    assert message in r.json()["detail"]


def test_blank_email_is_null(api):
    r = api.client.post("/customers", json={"name": "Acme", "contact_email": ""})
    assert r.json()["contact_email"] is None


def test_duplicate_name_is_409(api):
    api.customer("Acme Corp")
    r = api.client.post("/customers", json={"name": "Acme Corp"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_patch_customer(api):
    acme = api.customer("Acme Corp", )
    r = api.client.patch(f"/customers/{acme['id']}", json={"contact_email": "a@acme.com"})
    assert r.status_code == 200
    assert r.json()["contact_email"] == "a@acme.com" and r.json()["name"] == "Acme Corp"
    r = api.client.patch(f"/customers/{acme['id']}", json={"name": "Acme Inc", "contact_email": None})
    assert (r.json()["name"], r.json()["contact_email"]) == ("Acme Inc", None)


def test_patch_errors(api):
    api.customer("Taken")
    acme = api.customer("Acme")
    assert api.client.patch(f"/customers/{acme['id']}", json={"name": "Taken"}).status_code == 409
    assert api.client.patch(f"/customers/{acme['id']}", json={"name": ""}).status_code == 422
    r = api.client.patch("/customers/99", json={"name": "x"})
    assert r.status_code == 404 and r.json()["detail"] == "customer 99 not found"


def test_delete_customer_removes_groups_and_ungroups_members(api):
    acme = api.customer()
    shop = api.group(acme["id"])
    monitor = api.monitor(group_id=shop["id"])
    api.client.post(f"/groups/{shop['id']}/members", json={"target": "shop.acme.com",
                                                           "add_website": False})

    assert api.client.delete(f"/customers/{acme['id']}").status_code == 204
    assert api.client.get("/customers").json() == []
    assert api.client.get(f"/groups/{shop['id']}").status_code == 404
    assert api.client.get(f"/monitors/{monitor['id']}").json()["group_id"] is None
    assert api.client.get("/domains").json()[0]["group_id"] is None


def test_delete_customer_clears_customer_on_members(api):
    """Same as Postgres ON DELETE SET NULL: grouped and ungrouped members lose the customer."""
    acme = api.customer()
    shop = api.group(acme["id"])
    grouped = api.monitor(group_id=shop["id"])
    direct = api.monitor(name="Blog", url="https://blog.acme.com", customer_id=acme["id"])
    api.client.post(f"/groups/{shop['id']}/members", json={"target": "shop.acme.com",
                                                           "add_website": False})

    api.client.delete(f"/customers/{acme['id']}")

    for monitor in (grouped, direct):
        assert api.client.get(f"/monitors/{monitor['id']}").json()["customer_id"] is None
    assert api.client.get("/domains").json()[0]["customer_id"] is None


def test_delete_unknown_customer_is_404(api):
    assert api.client.delete("/customers/42").status_code == 404
