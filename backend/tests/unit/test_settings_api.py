"""/settings/alerts: the admin Settings page API."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.secret_box import SecretBox
from backend.ssl_monitor.adapters.storage.settings_memory import MemorySettingsStore
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import (
    get_alert_notifier,
    get_app_settings,
    get_current_user,
    get_secret_box,
    get_settings_store,
)
from backend.ssl_monitor.auth.models import User
from backend.ssl_monitor.config.settings import Settings

PASSWORD = "smtp-Secret-123"
SMTP = {"transport": "smtp", "smtp_host": "email-smtp.ap-south-1.amazonaws.com",
        "smtp_port": 465, "smtp_username": "AKIAEXAMPLE", "smtp_password": PASSWORD}


class RefusingNotifier(ConsoleNotifier):
    last_error = "554 Message rejected: Email address is not verified."

    def send_email(self, message):
        return False


@pytest.fixture
def client():
    settings = Settings.from_env(env={"ALERT_EMAIL": "env@x.com", "AWS_REGION": "ap-south-1"})
    store = MemorySettingsStore()
    box = SecretBox(Fernet.generate_key().decode())
    app.dependency_overrides.update({
        get_app_settings: lambda: settings,
        get_settings_store: lambda: store,
        get_secret_box: lambda: box,
        get_current_user: lambda: User("boss", "admin"),
    })
    yield TestClient(app)
    app.dependency_overrides.clear()


def as_viewer():
    app.dependency_overrides[get_current_user] = lambda: User("viewer", "user")


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/settings/alerts", None),
    ("PUT", "/settings/alerts", {"sender": "x@x.com"}),
    ("POST", "/settings/alerts/test-email", {"to": "x@x.com"}),
])
def test_non_admins_are_forbidden(client, method, path, body):
    as_viewer()
    assert client.request(method, path, json=body).status_code == 403


def test_get_shows_env_values(client):
    r = client.get("/settings/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["sender"] == "env@x.com"
    assert body["saved"]["sender"] is False
    assert body["transport"] == "iam_role"
    assert body["smtp_port"] == 587
    assert body["encryption_available"] is True
    assert body["updated_by"] is None


def test_put_then_get_shows_saved_values(client):
    r = client.put("/settings/alerts", json={"sender": "ui@x.com", "default_recipients": [
        "noc@x.com"], "reminder_minutes": 15})
    assert r.status_code == 200, r.text
    body = client.get("/settings/alerts").json()
    assert body["sender"] == "ui@x.com"
    assert body["default_recipients"] == ["noc@x.com"]
    assert body["saved"] == {"sender": True, "default_recipients": True,
                             "reminder_minutes": True, "alert_delay_minutes": False}
    assert body["updated_by"] == "boss"
    assert body["updated_at"] is not None


def test_password_is_write_only(client):
    r = client.put("/settings/alerts", json=SMTP)
    assert r.status_code == 200, r.text
    assert PASSWORD not in r.text
    r = client.get("/settings/alerts")
    assert r.json()["smtp_password_set"] is True
    assert r.json()["transport"] == "smtp"
    assert PASSWORD not in r.text
    assert "smtp_password\"" not in r.text


def test_null_reverts_to_env(client):
    client.put("/settings/alerts", json={"sender": "ui@x.com"})
    body = client.put("/settings/alerts", json={"sender": None}).json()
    assert body["sender"] == "env@x.com"
    assert body["saved"]["sender"] is False


@pytest.mark.parametrize("body", [
    {"sender": "nope"},
    {"reminder_minutes": 45},
    {"smtp_host": "smtp.gmail.com"},
    {"smtp_port": 2525},
    {"transport": "smtp"},
    {"transport": "pigeon"},
    {"surprise": True},
])
def test_put_rejects_bad_values(client, body):
    r = client.put("/settings/alerts", json=body)
    assert r.status_code == 422
    assert client.get("/settings/alerts").json()["updated_by"] is None


def test_put_password_without_encryption_key(client):
    app.dependency_overrides[get_secret_box] = lambda: None
    r = client.put("/settings/alerts", json={"smtp_password": PASSWORD})
    assert r.status_code == 422
    assert "SETTINGS_ENCRYPTION_KEY" in r.json()["detail"]
    assert PASSWORD not in r.text


def test_test_email_is_sent(client):
    r = client.post("/settings/alerts/test-email", json={"to": "me@x.com"})
    assert r.status_code == 200, r.text
    assert r.json()["message"].startswith("Test email sent to me@x.com via ")


def test_test_email_with_bad_address(client):
    assert client.post("/settings/alerts/test-email", json={"to": "nope"}).status_code == 422


def test_test_email_refused_is_502_with_the_provider_reply(client):
    app.dependency_overrides[get_alert_notifier] = RefusingNotifier
    r = client.post("/settings/alerts/test-email", json={"to": "me@x.com"})
    assert r.status_code == 502
    assert "Email address is not verified" in r.json()["detail"]
