"""/alerts/settings, /alert-rules and POST /monitors/{id}/test-alert (see conftest ``api``)."""

from __future__ import annotations

import pytest

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.cooldown import Cooldown
from backend.ssl_monitor.api.dependencies import (
    get_app_settings,
    get_current_user,
    get_alert_notifier,
)
from backend.ssl_monitor.api.routers.alerts import (
    TEST_ALERT_COOLDOWN_SECONDS,
    get_test_alert_cooldown,
)
from backend.ssl_monitor.auth.models import User
from backend.ssl_monitor.config.settings import Settings


class RefusingNotifier(ConsoleNotifier):
    def send_email(self, message) -> bool:
        return False


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clock(api) -> FakeClock:
    """A fresh test-alert cooldown for every test, on a clock the test moves."""
    fake = FakeClock()
    cooldown = Cooldown(TEST_ALERT_COOLDOWN_SECONDS, clock=fake)
    app.dependency_overrides[get_test_alert_cooldown] = lambda: cooldown
    return fake


@pytest.fixture
def notifier(api):
    sent = ConsoleNotifier()
    app.dependency_overrides[get_alert_notifier] = lambda: sent
    return sent


def _rule(api, **fields) -> dict:
    r = api.client.post("/alert-rules", json={"target": "ops@acme.com"} | fields)
    assert r.status_code == 201, r.text
    return r.json()


# ── Settings ─────────────────────────────────────────────────────


def test_alert_settings_defaults(api):
    assert api.client.get("/alerts/settings").json() == {
        "default_recipients": [], "reminder_minutes": 30, "alert_delay_minutes": 0,
        "notifier": "console", "sender": None,
    }


def test_alert_settings_from_env(api):
    settings = Settings.from_env({
        "ALERT_EMAIL": "Alerts@X.com", "ALERT_RECIPIENTS": "b@y.com, alerts@x.com",
        "UPTIME_REMINDER_MINUTES": "60", "UPTIME_ALERT_DELAY_MINUTES": "5",
        "NOTIFIER": "sns_ses",
    })
    app.dependency_overrides[get_app_settings] = lambda: settings
    assert api.client.get("/alerts/settings").json() == {
        "default_recipients": ["alerts@x.com", "b@y.com"], "reminder_minutes": 60,
        "alert_delay_minutes": 5, "notifier": "sns_ses", "sender": "Alerts@X.com",
    }


# ── Rules: create / list ─────────────────────────────────────────


def test_create_global_rule(api):
    body = _rule(api, target="  Ops@Acme.com ")
    assert body["id"] == 1
    assert body["target"] == "ops@acme.com"
    assert body["channel"] == "email"
    assert body["customer_id"] is None and body["monitor_id"] is None
    assert (body["on_down"], body["on_recovery"], body["enabled"]) == (True, True, True)
    assert body["created_at"] is not None


def test_rules_are_listed_per_scope(api):
    customer = api.customer()
    monitor = api.monitor()
    _rule(api, target="global@acme.com")
    _rule(api, target="cust@acme.com", customer_id=customer["id"], on_recovery=False)
    _rule(api, target="mon@acme.com", monitor_id=monitor["id"])

    def targets(query: str) -> list[str]:
        return [r["target"] for r in api.client.get(f"/alert-rules{query}").json()]

    assert targets("") == ["global@acme.com"]
    assert targets(f"?customer_id={customer['id']}") == ["cust@acme.com"]
    assert targets(f"?monitor_id={monitor['id']}") == ["mon@acme.com"]
    cust_rule = api.client.get(f"/alert-rules?customer_id={customer['id']}").json()[0]
    assert cust_rule["on_recovery"] is False


@pytest.mark.parametrize("fields, status, message", [
    ({"target": "not-an-email"}, 422, "invalid email"),
    ({"target": ""}, 422, "invalid email"),
    ({"channel": "sns"}, 422, "channel must be 'email'"),
    ({"customer_id": 1, "monitor_id": 1}, 422, "not both"),
    ({"customer_id": 99}, 404, "customer 99"),
    ({"monitor_id": 99}, 404, "monitor 99"),
])
def test_create_rule_errors(api, fields, status, message):
    api.customer()
    api.monitor()
    r = api.client.post("/alert-rules", json={"target": "ops@acme.com"} | fields)
    assert r.status_code == status
    assert message in r.json()["detail"]


def test_duplicate_rule_is_409(api):
    _rule(api)
    assert api.client.post("/alert-rules", json={"target": "OPS@acme.com"}).status_code == 409


def test_same_target_in_another_scope_is_fine(api):
    customer = api.customer()
    _rule(api)
    _rule(api, customer_id=customer["id"])


def test_list_with_both_scopes_is_422(api):
    assert api.client.get("/alert-rules?customer_id=1&monitor_id=1").status_code == 422


# ── Rules: update / delete ───────────────────────────────────────


def test_patch_rule_flags(api):
    rule = _rule(api)
    r = api.client.patch(f"/alert-rules/{rule['id']}", json={"enabled": False, "on_down": False})
    assert r.status_code == 200
    assert (r.json()["enabled"], r.json()["on_down"], r.json()["on_recovery"]) == (
        False, False, True)


def test_empty_patch_returns_the_rule(api):
    rule = _rule(api)
    assert api.client.patch(f"/alert-rules/{rule['id']}", json={}).json() == rule


def test_patch_null_flag_is_422(api):
    rule = _rule(api)
    r = api.client.patch(f"/alert-rules/{rule['id']}", json={"enabled": None})
    assert r.status_code == 422


def test_patch_and_delete_unknown_rule_are_404(api):
    assert api.client.patch("/alert-rules/9", json={"enabled": False}).status_code == 404
    assert api.client.patch("/alert-rules/9", json={}).status_code == 404
    assert api.client.delete("/alert-rules/9").status_code == 404


def test_delete_rule(api):
    rule = _rule(api)
    assert api.client.delete(f"/alert-rules/{rule['id']}").status_code == 204
    assert api.client.get("/alert-rules").json() == []


# ── Test alert ───────────────────────────────────────────────────


def test_test_alert_sends_to_rule_recipients(api, notifier):
    monitor = api.monitor()
    _rule(api, target="b@y.com")
    _rule(api, target="a@x.com", monitor_id=monitor["id"])
    r = api.client.post(f"/monitors/{monitor['id']}/test-alert")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "message": "Test alert sent to a@x.com, b@y.com"}
    assert notifier.sent_emails[0].subject == "[TEST] Shop"


def test_test_alert_includes_customer_and_group_in_subject(api, notifier):
    customer = api.customer()
    group = api.group(customer["id"])
    monitor = api.monitor(group_id=group["id"])
    _rule(api)
    api.client.post(f"/monitors/{monitor['id']}/test-alert")
    assert notifier.sent_emails[0].subject == "[TEST] Acme Corp · E-commerce · Shop"


def test_test_alert_unknown_monitor_is_404(api, notifier):
    assert api.client.post("/monitors/9/test-alert").status_code == 404


def test_test_alert_without_recipients_is_422(api, notifier):
    monitor = api.monitor()
    r = api.client.post(f"/monitors/{monitor['id']}/test-alert")
    assert r.status_code == 422
    assert "ALERT_EMAIL" in r.json()["detail"]


def test_test_alert_refused_by_provider_is_502(api):
    app.dependency_overrides[get_alert_notifier] = RefusingNotifier
    monitor = api.monitor()
    _rule(api)
    assert api.client.post(f"/monitors/{monitor['id']}/test-alert").status_code == 502


def test_test_alert_cooldown_per_monitor(api, notifier, clock):
    shop, blog = api.monitor(), api.monitor(name="Blog", url="https://blog.acme.com")
    _rule(api)
    assert api.client.post(f"/monitors/{shop['id']}/test-alert").status_code == 200

    clock.now += 10.5
    r = api.client.post(f"/monitors/{shop['id']}/test-alert")
    assert r.status_code == 429
    assert r.json()["detail"] == "Please wait 20 s before sending another test alert"
    assert r.headers["Retry-After"] == "20"
    assert api.client.post(f"/monitors/{blog['id']}/test-alert").status_code == 200

    clock.now += 19.5
    assert api.client.post(f"/monitors/{shop['id']}/test-alert").status_code == 200
    assert len(notifier.sent_emails) == 3


def test_refused_test_alert_also_starts_the_cooldown(api):
    app.dependency_overrides[get_alert_notifier] = RefusingNotifier
    monitor = api.monitor()
    _rule(api)
    assert api.client.post(f"/monitors/{monitor['id']}/test-alert").status_code == 502
    assert api.client.post(f"/monitors/{monitor['id']}/test-alert").status_code == 429


def test_failed_validation_does_not_start_the_cooldown(api, notifier):
    monitor = api.monitor()
    assert api.client.post(f"/monitors/{monitor['id']}/test-alert").status_code == 422
    _rule(api)
    assert api.client.post(f"/monitors/{monitor['id']}/test-alert").status_code == 200


# ── Auth ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("method, path", [
    ("get", "/alerts/settings"),
    ("get", "/alert-rules"),
    ("post", "/alert-rules"),
    ("patch", "/alert-rules/1"),
    ("delete", "/alert-rules/1"),
    ("post", "/monitors/1/test-alert"),
])
def test_alert_endpoints_require_auth(api, method, path):
    del app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_app_settings] = lambda: Settings.from_env(
        {"AUTH_ENABLED": "true"})
    assert getattr(api.client, method)(path).status_code == 401


# ── Who may manage global rules ──────────────────────────────────


def _as_viewer() -> None:
    app.dependency_overrides[get_current_user] = lambda: User("viewer", "user")


def test_non_admin_cannot_create_a_global_rule(api):
    _as_viewer()
    r = api.client.post("/alert-rules", json={"target": "outsider@evil.com"})
    assert r.status_code == 403
    assert api.client.get("/alert-rules").json() == []


def test_non_admin_can_manage_customer_and_monitor_rules(api):
    customer, monitor = api.customer(), api.monitor()
    _as_viewer()
    by_customer = _rule(api, customer_id=customer["id"])
    by_monitor = _rule(api, monitor_id=monitor["id"])
    assert api.client.patch(f"/alert-rules/{by_customer['id']}",
                            json={"on_down": False}).status_code == 200
    assert api.client.delete(f"/alert-rules/{by_monitor['id']}").status_code == 204


def test_non_admin_cannot_change_or_delete_a_global_rule(api):
    rule = _rule(api)  # created by the admin
    _as_viewer()
    assert api.client.patch(f"/alert-rules/{rule['id']}",
                            json={"enabled": False}).status_code == 403
    assert api.client.delete(f"/alert-rules/{rule['id']}").status_code == 403
    assert api.client.get("/alert-rules").json()[0]["enabled"] is True
