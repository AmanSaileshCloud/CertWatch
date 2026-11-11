"""/groups endpoints, one-click add, and domain ↔ group assignment (in-memory)."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.core.models import DomainRecord, Status

UTC = dt.timezone.utc


@pytest.fixture
def shop(api):
    acme = api.customer()
    return api.group(acme["id"])


def _put_domain(api, host: str, status: Status, group_id: int | None = None) -> DomainRecord:
    record = DomainRecord.new(host=host, port=443, created_at=dt.datetime.now(UTC))
    record.status = status
    record.group_id = group_id
    api.storage.put(record)
    return record


def _add(api, group_id: int, **body):
    return api.client.post(f"/groups/{group_id}/members", json={"target": "shop.acme.com"} | body)


# ── CRUD ─────────────────────────────────────────────────────────


def test_create_group_summary(api, shop):
    assert shop["name"] == "E-commerce"
    assert shop["customer_name"] == "Acme Corp"
    assert (shop["ssl_count"], shop["website_count"]) == (0, 0)
    assert (shop["health"], shop["problems"]) == ("empty", 0)
    assert shop["created_at"] is not None


def test_create_group_errors(api, shop):
    r = api.client.post("/groups", json={"customer_id": 99, "name": "x"})
    assert r.status_code == 404 and r.json()["detail"] == "customer 99 not found"
    r = api.client.post("/groups", json={"customer_id": shop["customer_id"], "name": "E-commerce"})
    assert r.status_code == 409
    r = api.client.post("/groups", json={"customer_id": shop["customer_id"], "name": " "})
    assert r.status_code == 422


def test_same_group_name_allowed_for_another_customer(api, shop):
    other = api.customer("Other")
    assert api.group(other["id"], "E-commerce")["customer_id"] == other["id"]


def test_list_groups_ordered_and_filtered(api):
    zed = api.customer("Zed")
    acme = api.customer("Acme")
    api.group(zed["id"], "Alpha")
    api.group(acme["id"], "Web")
    api.group(acme["id"], "Api")
    body = api.client.get("/groups").json()
    assert [(g["customer_name"], g["name"]) for g in body] == [
        ("Acme", "Api"), ("Acme", "Web"), ("Zed", "Alpha")]
    filtered = api.client.get("/groups", params={"customer_id": zed["id"]}).json()
    assert [g["name"] for g in filtered] == ["Alpha"]


def test_rename_group(api, shop):
    r = api.client.patch(f"/groups/{shop['id']}", json={"name": "Shop"})
    assert r.status_code == 200 and r.json()["name"] == "Shop"
    api.group(shop["customer_id"], "Blog")
    assert api.client.patch(f"/groups/{shop['id']}", json={"name": "Blog"}).status_code == 409
    assert api.client.patch(f"/groups/{shop['id']}", json={"name": ""}).status_code == 422
    assert api.client.patch("/groups/99", json={"name": "x"}).status_code == 404


def test_get_group_detail(api, shop):
    _add(api, shop["id"])
    body = api.client.get(f"/groups/{shop['id']}").json()
    assert [d["domain"] for d in body["domains"]] == ["shop.acme.com:443"]
    assert [m["name"] for m in body["monitors"]] == ["shop.acme.com"]
    assert body["monitors"][0]["group_name"] == "E-commerce"
    assert (body["ssl_count"], body["website_count"], body["health"]) == (1, 1, "ok")
    assert api.client.get("/groups/99").status_code == 404


def test_delete_group_ungroups_members(api, shop):
    _add(api, shop["id"])
    assert api.client.delete(f"/groups/{shop['id']}").status_code == 204
    assert api.client.get(f"/groups/{shop['id']}").status_code == 404
    [domain] = api.client.get("/domains").json()
    [monitor] = api.client.get("/monitors").json()
    assert domain["group_id"] is None and monitor["group_id"] is None  # kept, not deleted
    assert api.client.delete(f"/groups/{shop['id']}").status_code == 404


# ── Health ───────────────────────────────────────────────────────


def _health(api, group_id):
    body = api.client.get(f"/groups/{group_id}").json()
    return body["health"], body["problems"]


def test_health_ok_with_unknown_and_paused_websites(api, shop):
    api.monitor(group_id=shop["id"])
    paused = api.monitor(name="Paused", group_id=shop["id"])
    api.take_down(paused["id"])
    api.client.post(f"/monitors/{paused['id']}/pause")
    assert _health(api, shop["id"]) == ("ok", 0)


def test_health_warning_from_ssl(api, shop):
    _put_domain(api, "a.acme.com", Status.OK, shop["id"])
    _put_domain(api, "b.acme.com", Status.WARNING, shop["id"])
    assert _health(api, shop["id"]) == ("warning", 1)


@pytest.mark.parametrize("ssl_status", [Status.EXPIRED, Status.UNREACHABLE])
def test_health_down_from_ssl(api, shop, ssl_status):
    _put_domain(api, "a.acme.com", ssl_status, shop["id"])
    _put_domain(api, "b.acme.com", Status.WARNING, shop["id"])
    assert _health(api, shop["id"]) == ("down", 2)


def test_health_down_from_website(api, shop):
    monitor = api.monitor(group_id=shop["id"])
    _put_domain(api, "a.acme.com", Status.OK, shop["id"])
    api.take_down(monitor["id"])
    assert _health(api, shop["id"]) == ("down", 1)
    [summary] = api.client.get("/groups").json()
    assert (summary["health"], summary["problems"], summary["website_count"]) == ("down", 1, 1)


# ── One-click add ────────────────────────────────────────────────


def test_add_both(api, shop):
    r = _add(api, shop["id"])
    assert r.status_code == 201
    body = r.json()
    assert body["messages"] == []
    domain, monitor = body["domain"], body["monitor"]
    assert domain["domain"] == "shop.acme.com:443"
    assert domain["status"] == "ok"  # probed straight away
    assert (domain["group_id"], domain["customer_id"]) == (shop["id"], shop["customer_id"])
    assert monitor["name"] == "shop.acme.com" and monitor["url"] == "https://shop.acme.com"
    assert monitor["interval_seconds"] == 300
    assert (monitor["group_id"], monitor["customer_id"]) == (shop["id"], shop["customer_id"])


def test_add_from_url_keeps_url_and_https_port(api, shop):
    body = _add(api, shop["id"], target="https://shop.acme.com:8443/health",
                interval_seconds=60).json()
    assert body["domain"]["domain"] == "shop.acme.com:8443"
    assert body["monitor"]["url"] == "https://shop.acme.com:8443/health"
    assert body["monitor"]["interval_seconds"] == 60


def test_add_from_http_url_uses_443_for_ssl(api, shop):
    body = _add(api, shop["id"], target="http://shop.acme.com:8080/").json()
    assert body["domain"]["domain"] == "shop.acme.com:443"


def test_add_ssl_only(api, shop):
    body = _add(api, shop["id"], add_website=False).json()
    assert body["domain"] is not None and body["monitor"] is None
    assert api.client.get("/monitors").json() == []


def test_add_website_only(api, shop):
    body = _add(api, shop["id"], add_ssl=False).json()
    assert body["domain"] is None and body["monitor"] is not None
    assert api.client.get("/domains").json() == []


def test_add_neither_is_422(api, shop):
    r = _add(api, shop["id"], add_ssl=False, add_website=False)
    assert r.status_code == 422
    assert "at least one" in r.json()["detail"]


def test_add_existing_domain_moves_it(api, shop):
    _put_domain(api, "shop.acme.com", Status.OK)
    body = _add(api, shop["id"]).json()
    assert body["messages"] == ["shop.acme.com:443 was already monitored — moved into E-commerce"]
    assert body["domain"]["group_id"] == shop["id"]
    assert len(api.client.get("/domains").json()) == 1


def test_add_bare_host_with_port(api, shop):
    body = _add(api, shop["id"], target="shop.acme.com:8443").json()
    assert body["domain"]["domain"] == "shop.acme.com:8443"
    assert body["monitor"]["url"] == "https://shop.acme.com:8443"
    assert body["monitor"]["name"] == "shop.acme.com"


def test_add_twice_keeps_one_monitor(api, shop):
    _add(api, shop["id"])
    body = _add(api, shop["id"], target="https://shop.acme.com/").json()
    assert len(api.client.get("/monitors").json()) == 1
    assert "https://shop.acme.com was already monitored — moved into E-commerce" in (
        body["messages"])


def test_add_moves_existing_monitor_from_another_group(api, shop):
    blog = api.group(api.customer("Other")["id"], "Blog")
    monitor = api.monitor(url="https://shop.acme.com", group_id=blog["id"])
    api.take_down(monitor["id"])

    body = _add(api, shop["id"], add_ssl=False).json()

    assert body["messages"] == [
        "https://shop.acme.com was already monitored — moved into E-commerce"]
    assert body["monitor"]["id"] == monitor["id"]
    assert (body["monitor"]["group_id"], body["monitor"]["customer_id"]) == (
        shop["id"], shop["customer_id"])
    assert body["monitor"]["status"] == "down"  # moved, not reset
    assert len(api.client.get("/monitors").json()) == 1


def test_add_domain_race_is_409(api, shop, monkeypatch):
    """Another request adds the domain between our existence check and the insert."""
    real_get = api.storage.get
    calls = []

    def get_racing(key):
        calls.append(key)
        if len(calls) == 2:  # add_domain's own check sees the concurrent insert
            _put_domain(api, "shop.acme.com", Status.OK)
        return real_get(key)

    monkeypatch.setattr(api.storage, "get", get_racing)
    r = _add(api, shop["id"], add_website=False)
    assert r.status_code == 409
    assert "already monitored" in r.json()["detail"]


def test_add_blocked_url_is_422_and_adds_nothing(api, shop):
    api.dns.hosts["intranet.acme.com"] = ["10.0.0.8"]
    r = _add(api, shop["id"], target="intranet.acme.com")
    assert r.status_code == 422
    assert "private or internal address (10.0.0.8)" in r.json()["detail"]
    assert api.client.get("/domains").json() == []
    assert api.client.get("/monitors").json() == []


@pytest.mark.parametrize("target", ["", "bad host", "ftp://shop.acme.com"])
def test_add_invalid_target_is_422(api, shop, target):
    assert _add(api, shop["id"], target=target).status_code == 422
    assert api.client.get("/domains").json() == []


def test_add_bad_interval_adds_nothing(api, shop):
    assert _add(api, shop["id"], interval_seconds=7).status_code == 422
    assert api.client.get("/domains").json() == []


def test_add_to_unknown_group_is_404(api):
    assert _add(api, 99).status_code == 404


# ── Domains ↔ groups ─────────────────────────────────────────────


def test_domains_include_owner_fields_and_filter(api, shop):
    _add(api, shop["id"], add_website=False)
    api.client.post("/domains", json={"domain": "loose.example.com"})
    all_domains = api.client.get("/domains").json()
    assert {d["domain"]: d["group_id"] for d in all_domains} == {
        "loose.example.com:443": None, "shop.acme.com:443": shop["id"]}
    grouped = api.client.get("/domains", params={"group_id": shop["id"]}).json()
    assert [d["domain"] for d in grouped] == ["shop.acme.com:443"]
    assert grouped[0]["customer_id"] == shop["customer_id"]


def test_patch_domain_group_sets_customer_and_null_ungroups(api, shop):
    api.client.post("/domains", json={"domain": "loose.example.com"})
    r = api.client.patch("/domains/loose.example.com:443", json={"group_id": shop["id"]})
    assert r.status_code == 200
    assert (r.json()["group_id"], r.json()["customer_id"]) == (shop["id"], shop["customer_id"])

    r = api.client.patch("/domains/loose.example.com:443", json={"alerts_enabled": False})
    assert r.json()["group_id"] == shop["id"]  # absent group_id leaves it alone
    assert r.json()["alerts_enabled"] is False

    r = api.client.patch("/domains/loose.example.com:443", json={"group_id": None})
    assert r.json()["group_id"] is None and r.json()["customer_id"] == shop["customer_id"]


def test_patch_domain_group_errors(api, shop):
    api.client.post("/domains", json={"domain": "loose.example.com"})
    r = api.client.patch("/domains/loose.example.com:443", json={"group_id": 99})
    assert r.status_code == 404 and r.json()["detail"] == "group 99 not found"
    r = api.client.patch("/domains/nope.example.com:443", json={"group_id": shop["id"]})
    assert r.status_code == 404
