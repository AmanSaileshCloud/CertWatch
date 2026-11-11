"""/monitors endpoints against in-memory repos (see conftest ``api`` fixture)."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import get_app_settings
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.alert_models import AlertKind

UTC = dt.timezone.utc


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ── Create / read ────────────────────────────────────────────────


def test_create_monitor_with_defaults(api):
    body = api.monitor()
    assert body["id"] == 1
    assert body["url"] == "https://shop.acme.com"
    assert body["type"] == "http"
    assert (body["interval_seconds"], body["timeout_seconds"], body["expected_status"]) == (300, 10, 200)
    assert body["keyword"] is None
    assert body["status"] == "unknown"
    assert body["is_paused"] is False
    assert body["uptime_24h"] is None and body["down_since"] is None
    assert body["customer_id"] is None and body["group_name"] is None
    assert body["created_at"] is not None
    stored = api.repo.get_monitor(1)
    assert stored.next_check_at <= dt.datetime.now(UTC)  # checked straight away


def test_create_monitor_with_all_fields(api):
    body = api.monitor(name="  Shop ", url="HTTPS://Shop.Acme.com/health", interval_seconds=60,
                       timeout_seconds=30, expected_status=204, keyword="Welcome")
    assert body["name"] == "Shop"
    assert body["url"] == "https://shop.acme.com/health"
    assert (body["interval_seconds"], body["timeout_seconds"], body["expected_status"]) == (60, 30, 204)
    assert body["keyword"] == "Welcome"


def test_empty_keyword_is_stored_as_null(api):
    assert api.monitor(keyword="")["keyword"] is None


@pytest.mark.parametrize("field, value, message", [
    ("name", "   ", "name must not be blank"),
    ("name", "x" * 201, "at most 200"),
    ("name", "Shop\nBcc: evil@x.com", "control characters"),
    ("name", "Shop\x1b[31m", "control characters"),
    ("url", "ftp://shop.acme.com", "http:// or https://"),
    ("url", "https://u:p@shop.acme.com", "username or password"),
    ("url", "https://shop.acme.com/" + "a" * 2000, "2000"),
    ("interval_seconds", 120, "60, 300, 600"),
    ("timeout_seconds", 0, "between 1 and 60"),
    ("timeout_seconds", 61, "between 1 and 60"),
    ("expected_status", 99, "between 100 and 599"),
    ("expected_status", 600, "between 100 and 599"),
    ("keyword", "k" * 201, "at most 200"),
])
def test_create_validation_errors(api, field, value, message):
    r = api.client.post("/monitors", json={"name": "Shop", "url": "https://shop.acme.com",
                                           field: value})
    assert r.status_code == 422
    assert message in r.json()["detail"]


def test_missing_required_field_is_422(api):
    assert api.client.post("/monitors", json={"name": "Shop"}).status_code == 422


@pytest.mark.parametrize("url, address", [
    ("http://169.254.169.254/latest/meta-data", "169.254.169.254"),
    ("http://localhost:8080", "127.0.0.1"),
    ("http://[::1]/", "::1"),
    ("http://10.0.0.5/", "10.0.0.5"),
])
def test_internal_targets_are_blocked(api, url, address):
    r = api.client.post("/monitors", json={"name": "x", "url": url})
    assert r.status_code == 422
    assert r.json()["detail"] == (
        f"URL points to a private or internal address ({address}); set "
        "UPTIME_ALLOW_PRIVATE_TARGETS=true to allow internal sites."
    )


def test_hostname_resolving_privately_is_blocked(api):
    api.dns.hosts["intranet.acme.com"] = ["192.168.10.4"]
    r = api.client.post("/monitors", json={"name": "x", "url": "https://intranet.acme.com"})
    assert r.status_code == 422


def test_dns_failure_is_accepted(api):
    assert api.monitor(url="https://not-yet-live.example")["status"] == "unknown"


def test_private_targets_allowed_by_setting(api):
    from dataclasses import replace

    settings = replace(Settings.from_env(env={}), uptime_allow_private_targets=True)
    app.dependency_overrides[get_app_settings] = lambda: settings
    assert api.monitor(url="http://localhost:8080")["url"] == "http://localhost:8080"


def test_get_and_list_monitors(api):
    api.monitor(name="Zeta")
    api.monitor(name="alpha")
    api.monitor(name="Beta")
    names = [m["name"] for m in api.client.get("/monitors").json()]
    assert names == sorted(names)  # by name (repo order)
    assert api.client.get("/monitors/2").json()["name"] == "alpha"
    r = api.client.get("/monitors/99")
    assert r.status_code == 404
    assert r.json()["detail"] == "monitor 99 not found"


def test_list_filters_by_group_and_customer(api):
    acme = api.customer()
    other = api.customer("Other")
    shop = api.group(acme["id"])
    api.monitor(name="in-group", group_id=shop["id"])
    api.monitor(name="customer-only", customer_id=other["id"])
    api.monitor(name="loose")
    by_group = api.client.get("/monitors", params={"group_id": shop["id"]}).json()
    assert [m["name"] for m in by_group] == ["in-group"]
    by_customer = api.client.get("/monitors", params={"customer_id": acme["id"]}).json()
    assert [m["name"] for m in by_customer] == ["in-group"]
    by_other = api.client.get("/monitors", params={"customer_id": other["id"]}).json()
    assert [m["name"] for m in by_other] == ["customer-only"]


# ── Ownership ────────────────────────────────────────────────────


def test_group_sets_customer_and_names(api):
    acme = api.customer()
    shop = api.group(acme["id"])
    body = api.monitor(group_id=shop["id"])
    assert body["group_id"] == shop["id"] and body["group_name"] == "E-commerce"
    assert body["customer_id"] == acme["id"] and body["customer_name"] == "Acme Corp"


def test_null_customer_with_group_takes_the_groups_customer(api):
    acme = api.customer()
    shop = api.group(acme["id"])
    body = api.monitor(group_id=shop["id"], customer_id=None)
    assert (body["group_id"], body["customer_id"]) == (shop["id"], acme["id"])
    blog = api.group(acme["id"], "Blog")
    r = api.client.patch(f"/monitors/{body['id']}", json={"group_id": blog["id"], "customer_id": None})
    assert r.status_code == 200
    assert (r.json()["group_id"], r.json()["customer_id"]) == (blog["id"], acme["id"])


def test_unknown_group_or_customer_is_404(api):
    r = api.client.post("/monitors", json={"name": "x", "url": "https://x.com", "group_id": 9})
    assert r.status_code == 404 and r.json()["detail"] == "group 9 not found"
    r = api.client.post("/monitors", json={"name": "x", "url": "https://x.com", "customer_id": 9})
    assert r.status_code == 404 and r.json()["detail"] == "customer 9 not found"


def test_customer_conflicting_with_group_is_422(api):
    acme = api.customer()
    other = api.customer("Other")
    shop = api.group(acme["id"])
    r = api.client.post("/monitors", json={"name": "x", "url": "https://x.com",
                                           "group_id": shop["id"], "customer_id": other["id"]})
    assert r.status_code == 422
    monitor = api.monitor(group_id=shop["id"])
    r = api.client.patch(f"/monitors/{monitor['id']}", json={"customer_id": other["id"]})
    assert r.status_code == 422


def test_patch_group_moves_customer_and_null_ungroups(api):
    acme = api.customer()
    other = api.customer("Other")
    shop = api.group(acme["id"])
    blog = api.group(other["id"], "Blog")
    monitor = api.monitor(group_id=shop["id"])
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"group_id": blog["id"]}).json()
    assert (body["group_id"], body["customer_id"]) == (blog["id"], other["id"])
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"group_id": None}).json()
    assert body["group_id"] is None and body["customer_id"] == other["id"]  # customer kept


# ── State rules ──────────────────────────────────────────────────


def test_pause_closes_open_incident_and_resets(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    assert api.client.get(f"/monitors/{monitor['id']}").json()["status"] == "down"

    body = api.client.post(f"/monitors/{monitor['id']}/pause").json()
    assert body["is_paused"] is True and body["status"] == "paused"
    assert body["down_since"] is None
    stored = api.repo.get_monitor(monitor["id"])
    assert stored.state.value == "unknown" and stored.consecutive_failures == 0
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["close_reason"] == "paused" and incident["resolved_at"] is not None


def test_resume_schedules_a_check_now(api):
    monitor = api.monitor()
    api.client.post(f"/monitors/{monitor['id']}/pause")
    before = dt.datetime.now(UTC)
    body = api.client.post(f"/monitors/{monitor['id']}/resume").json()
    assert body["is_paused"] is False and body["status"] == "unknown"
    stored = api.repo.get_monitor(monitor["id"])
    assert before <= stored.next_check_at <= dt.datetime.now(UTC)


def test_resume_of_unpaused_monitor_is_a_no_op(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    before = api.repo.get_monitor(monitor["id"])

    body = api.client.post(f"/monitors/{monitor['id']}/resume").json()

    assert body["status"] == "down"
    assert api.repo.get_monitor(monitor["id"]) == before
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["resolved_at"] is None


def test_pause_of_paused_monitor_is_a_no_op(api):
    monitor = api.monitor()
    paused = api.client.post(f"/monitors/{monitor['id']}/pause").json()
    stored = api.repo.get_monitor(monitor["id"])
    assert api.client.post(f"/monitors/{monitor['id']}/pause").json() == paused
    assert api.repo.get_monitor(monitor["id"]) == stored


def test_pause_and_resume_unknown_monitor_is_404(api):
    assert api.client.post("/monitors/5/pause").status_code == 404
    assert api.client.post("/monitors/5/resume").status_code == 404


@pytest.mark.parametrize("change", [
    {"url": "https://shop.acme.com/v2"},
    {"expected_status": 301},
    {"keyword": "Hello"},
    {"timeout_seconds": 20},
])
def test_target_edit_closes_incident_as_edited_and_resets(api, change):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    body = api.client.patch(f"/monitors/{monitor['id']}", json=change).json()
    assert body["status"] == "unknown"
    for key, value in change.items():
        assert body[key] == value
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["close_reason"] == "edited"


def test_interval_only_edit_reschedules_without_reset(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    api.repo.claim_due_monitors(dt.datetime.now(UTC), 10)  # pushes next_check_at out
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"interval_seconds": 60}).json()
    assert body["interval_seconds"] == 60 and body["status"] == "down"
    assert api.repo.get_monitor(monitor["id"]).next_check_at <= dt.datetime.now(UTC)
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["resolved_at"] is None


def test_rename_does_not_reset(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"name": "Shop EU"}).json()
    assert body["name"] == "Shop EU" and body["status"] == "down"


def test_same_url_is_not_a_change(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"url": monitor["url"]}).json()
    assert body["status"] == "down"


def test_patch_keyword_null_clears_it(api):
    monitor = api.monitor(keyword="Welcome")
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"keyword": None}).json()
    assert body["keyword"] is None
    monitor = api.monitor(keyword="Welcome")
    assert api.client.patch(f"/monitors/{monitor['id']}", json={"keyword": ""}).json()["keyword"] is None


def test_keyword_is_trimmed_and_blank_is_null(api):
    monitor = api.monitor(keyword="  Welcome ")
    assert monitor["keyword"] == "Welcome"
    assert api.monitor(name="B", keyword="   ")["keyword"] is None
    api.take_down(monitor["id"])
    body = api.client.patch(f"/monitors/{monitor['id']}", json={"keyword": "Welcome "}).json()
    assert body["status"] == "down"  # trimmed value is unchanged: no reset


def test_patch_validation_and_404(api):
    monitor = api.monitor()
    r = api.client.patch(f"/monitors/{monitor['id']}", json={"name": None})
    assert r.status_code == 422
    r = api.client.patch(f"/monitors/{monitor['id']}", json={"url": "http://127.0.0.1"})
    assert r.status_code == 422
    assert api.client.patch("/monitors/99", json={"name": "x"}).status_code == 404


# ── Status, uptime, history ──────────────────────────────────────


def test_down_status_and_down_since(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    body = api.client.get(f"/monitors/{monitor['id']}").json()
    assert body["status"] == "down"
    assert body["down_since"] is not None
    assert body["last_status_code"] == 503 and body["last_error"] == "Expected 200, got 503"


def test_paused_wins_over_down(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    api.repo.change_monitor(monitor["id"], {"is_paused": True}, now=dt.datetime.now(UTC))
    body = api.client.get(f"/monitors/{monitor['id']}").json()
    assert body["status"] == "paused" and body["down_since"] is None


def test_up_status_and_uptime_24h(api):
    monitor = api.monitor()
    for _ in range(2):
        api.record(monitor["id"], up=True, minutes_ago=10)
    api.record(monitor["id"], up=False, minutes_ago=5)
    api.record(monitor["id"], up=True, minutes_ago=60 * 25)  # outside the window
    body = api.client.get(f"/monitors/{monitor['id']}").json()
    assert body["uptime_24h"] == 66.67
    assert body["status"] in ("up", "unknown")
    assert api.client.get("/monitors").json()[0]["uptime_24h"] == 66.67


def test_checks_newest_first_with_limit(api):
    monitor = api.monitor()
    for minutes in (30, 20, 10):
        api.record(monitor["id"], up=True, minutes_ago=minutes)
    checks = api.client.get(f"/monitors/{monitor['id']}/checks", params={"limit": 2}).json()
    assert len(checks) == 2
    assert _parse(checks[0]["checked_at"]) > _parse(checks[1]["checked_at"])
    assert set(checks[0]) == {"checked_at", "is_up", "status_code", "response_ms", "error", "region"}
    assert checks[0]["region"] == "default"


@pytest.mark.parametrize("path, params", [
    ("checks", {"limit": 0}), ("checks", {"limit": 501}),
    ("incidents", {"limit": 0}), ("series", {"hours": 0}), ("series", {"hours": 169}),
])
def test_history_query_bounds(api, path, params):
    monitor = api.monitor()
    assert api.client.get(f"/monitors/{monitor['id']}/{path}", params=params).status_code == 422


def test_series_oldest_first_within_window(api):
    monitor = api.monitor()
    for minutes in (200, 90, 30):
        api.record(monitor["id"], up=True, minutes_ago=minutes)
    series = api.client.get(f"/monitors/{monitor['id']}/series", params={"hours": 2}).json()
    assert len(series) == 2
    assert _parse(series[0]["checked_at"]) < _parse(series[1]["checked_at"])


def test_history_of_unknown_monitor_is_404(api):
    for path in ("checks", "series", "incidents"):
        assert api.client.get(f"/monitors/7/{path}").status_code == 404


def test_incident_shape(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["monitor_id"] == monitor["id"]
    assert incident["resolved_at"] is None and incident["duration_seconds"] is None
    assert incident["reason"] == "Expected 200, got 503"
    assert incident["close_reason"] is None


def test_delete_monitor(api):
    monitor = api.monitor()
    assert api.client.delete(f"/monitors/{monitor['id']}").status_code == 204
    assert api.client.get(f"/monitors/{monitor['id']}").status_code == 404
    assert api.client.delete(f"/monitors/{monitor['id']}").status_code == 404


# ── Alert settings per monitor ───────────────────────────────────


def test_new_monitor_alert_fields_default_to_global(api):
    body = api.monitor()
    assert body["alerts_enabled"] is True
    assert body["reminder_minutes"] is None and body["alert_delay_minutes"] is None
    assert body["effective_reminder_minutes"] == 30
    assert body["effective_alert_delay_minutes"] == 0


def test_create_monitor_with_alert_overrides(api):
    body = api.monitor(alerts_enabled=False, reminder_minutes=0, alert_delay_minutes=15)
    assert (body["alerts_enabled"], body["reminder_minutes"], body["alert_delay_minutes"]) == (
        False, 0, 15)
    assert (body["effective_reminder_minutes"], body["effective_alert_delay_minutes"]) == (0, 15)


@pytest.mark.parametrize("field, value, message", [
    ("reminder_minutes", 45, "reminder_minutes must be null or one of 0, 15, 30, 60, 120"),
    ("reminder_minutes", -1, "reminder_minutes must be null"),
    ("alert_delay_minutes", 7, "alert_delay_minutes must be null or one of 0, 5, 10, 15, 30"),
])
def test_alert_field_validation(api, field, value, message):
    r = api.client.post("/monitors", json={"name": "Shop", "url": "https://shop.acme.com",
                                           field: value})
    assert r.status_code == 422
    assert message in r.json()["detail"]
    monitor = api.monitor()
    r = api.client.patch(f"/monitors/{monitor['id']}", json={field: value})
    assert r.status_code == 422


def test_patch_alerts_enabled_null_is_422(api):
    monitor = api.monitor()
    r = api.client.patch(f"/monitors/{monitor['id']}", json={"alerts_enabled": None})
    assert r.status_code == 422
    assert "alerts_enabled must be true or false" in r.json()["detail"]


def test_patch_alert_fields_keeps_state_and_open_incident(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    r = api.client.patch(f"/monitors/{monitor['id']}", json={
        "alerts_enabled": False, "reminder_minutes": 120, "alert_delay_minutes": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "down"
    assert (body["effective_reminder_minutes"], body["effective_alert_delay_minutes"]) == (120, 5)
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["resolved_at"] is None


def test_patch_null_resets_to_global_default(api):
    monitor = api.monitor(reminder_minutes=60)
    r = api.client.patch(f"/monitors/{monitor['id']}", json={"reminder_minutes": None})
    assert r.json()["reminder_minutes"] is None
    assert r.json()["effective_reminder_minutes"] == 30


def test_effective_values_follow_global_settings(api):
    settings = Settings.from_env({"UPTIME_REMINDER_MINUTES": "15",
                                  "UPTIME_ALERT_DELAY_MINUTES": "10"})
    app.dependency_overrides[get_app_settings] = lambda: settings
    body = api.monitor()
    assert (body["effective_reminder_minutes"], body["effective_alert_delay_minutes"]) == (15, 10)


def test_incident_alert_fields(api):
    monitor = api.monitor()
    api.take_down(monitor["id"])
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert incident["down_alert_sent_at"] is None
    assert incident["recovery_alert_sent_at"] is None
    assert incident["last_reminder_sent_at"] is None
    assert incident["reminder_count"] == 0

    sent_at = dt.datetime(2026, 1, 1, tzinfo=UTC)
    api.repo.mark_alert_sent(incident["id"], AlertKind.DOWN, sent_at)
    api.repo.mark_alert_sent(incident["id"], AlertKind.REMINDER, sent_at)
    [incident] = api.client.get(f"/monitors/{monitor['id']}/incidents").json()
    assert _parse(incident["down_alert_sent_at"]) == sent_at
    assert _parse(incident["last_reminder_sent_at"]) == sent_at
    assert incident["reminder_count"] == 1
