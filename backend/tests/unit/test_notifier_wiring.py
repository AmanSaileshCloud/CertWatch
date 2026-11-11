"""handlers._deps: which notifier the saved email transport selects."""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography.fernet import Fernet

from backend.ssl_monitor.adapters.notifier import sns_ses
from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.notifier.ses_smtp import SesSmtpNotifier
from backend.ssl_monitor.adapters.notifier.sns_ses import SnsSesNotifier
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.handlers import _deps
from backend.ssl_monitor.services.app_settings import update_alert_settings

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)
KEY = Fernet.generate_key().decode()
SMTP = {"transport": "smtp", "smtp_host": "email-smtp.ap-south-1.amazonaws.com",
        "smtp_username": "AKIAEXAMPLE", "smtp_password": "smtp-Secret-123"}


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    monkeypatch.setattr(sns_ses.boto3, "client", lambda *a, **k: object())
    monkeypatch.setattr(_deps, "_MEMORY_SETTINGS_STORE", None)


def make_settings(notifier: str = "sns_ses") -> Settings:
    return Settings.from_env(env={"NOTIFIER": notifier, "STORAGE": "memory",
                                  "ALERT_EMAIL": "env@x.com", "SETTINGS_ENCRYPTION_KEY": KEY})


def save(settings: Settings, **changes) -> None:
    update_alert_settings(_deps.make_settings_store(settings), settings,
                          _deps.make_secret_box(settings), changes, user="admin", now=NOW)


def test_console_setting_always_logs():
    settings = make_settings("console")
    save(settings, **SMTP)
    assert isinstance(_deps.make_notifier(settings), ConsoleNotifier)


def test_iam_role_uses_the_ses_api_with_saved_recipients():
    settings = make_settings()
    save(settings, sender="ui@x.com", default_recipients=["noc@x.com"])
    notifier = _deps.make_notifier(settings)
    assert isinstance(notifier, SnsSesNotifier)
    assert notifier._settings.alert_email == "ui@x.com"
    assert notifier._settings.alert_recipients == ["noc@x.com"]


def test_smtp_transport_uses_ses_smtp():
    settings = make_settings()
    save(settings, **SMTP)
    assert isinstance(_deps.make_notifier(settings), SesSmtpNotifier)


def test_alert_config_is_read_fresh_each_call():
    settings = make_settings()
    assert _deps.make_alert_config(settings).alert_email == "env@x.com"
    save(settings, sender="ui@x.com")
    assert _deps.make_alert_config(settings).alert_email == "ui@x.com"


def test_falls_back_to_env_when_settings_cannot_be_loaded(monkeypatch, caplog):
    def broken(_settings):
        raise RuntimeError("database is down")

    monkeypatch.setattr(_deps, "make_alert_config", broken)
    notifier = _deps.make_notifier(make_settings())
    assert isinstance(notifier, SnsSesNotifier)
    assert notifier._settings.alert_email == "env@x.com"
    assert "database is down" in caplog.text


@pytest.mark.parametrize("notifier,changes,expected", [
    ("console", SMTP, "the server log (NOTIFIER=console)"),
    ("sns_ses", SMTP, "SES SMTP"),
    ("sns_ses", {}, "SES (IAM role)"),
])
def test_delivery_method(notifier, changes, expected):
    settings = make_settings(notifier)
    save(settings, **changes)
    assert _deps.delivery_method(_deps.make_alert_config(settings), settings) == expected
