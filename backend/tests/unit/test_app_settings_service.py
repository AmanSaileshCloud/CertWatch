"""services.app_settings: resolution (UI → env → default), validation and the test email."""

from __future__ import annotations

import datetime as dt
import json
import logging

import pytest
from cryptography.fernet import Fernet

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.secret_box import SecretBox
from backend.ssl_monitor.adapters.storage.settings_memory import MemorySettingsStore
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.app_settings_models import EmailTransport, SettingKey
from backend.ssl_monitor.services import app_settings
from backend.ssl_monitor.services.app_settings import (
    NO_ENCRYPTION_KEY,
    TEST_EMAIL_SUBJECT,
    EmailNotDelivered,
    describe_alert_settings,
    load_alert_config,
    send_test_email,
    update_alert_settings,
)
from backend.ssl_monitor.services.errors import InvalidInput

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)
PASSWORD = "smtp-Secret-123"
SMTP = {"transport": "smtp", "smtp_host": "email-smtp.ap-south-1.amazonaws.com",
        "smtp_username": "AKIAEXAMPLE", "smtp_password": PASSWORD}
ENV = {
    "ALERT_EMAIL": "env-sender@x.com",
    "ALERT_RECIPIENTS": "env-ops@x.com",
    "UPTIME_REMINDER_MINUTES": "60",
    "UPTIME_ALERT_DELAY_MINUTES": "5",
    "AWS_REGION": "ap-south-1",
    "CERTWATCH_PUBLIC_URL": "https://cw.x.com",
}


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env(env=ENV)


@pytest.fixture
def box() -> SecretBox:
    return SecretBox(Fernet.generate_key().decode())


@pytest.fixture
def store() -> MemorySettingsStore:
    return MemorySettingsStore()


@pytest.fixture(autouse=True)
def fresh_warnings():
    app_settings._warn_once.cache_clear()


def update(store, settings, box, **changes):
    return update_alert_settings(store, settings, box, changes, user="admin", now=NOW)


# ── Resolution ──────────────────────────────────────────────────────────────


def test_env_values_when_nothing_is_saved(store, settings, box):
    config = load_alert_config(store, settings, box)
    assert config.alert_email == "env-sender@x.com"
    assert config.alert_recipients == ["env-ops@x.com"]
    assert (config.uptime_reminder_minutes, config.uptime_alert_delay_minutes) == (60, 5)
    assert config.public_url == "https://cw.x.com"
    assert config.transport is EmailTransport.IAM_ROLE
    assert (config.smtp_host, config.smtp_port, config.smtp_password) == (None, 587, None)


def test_defaults_without_env(store, box):
    config = load_alert_config(store, Settings.from_env(env={}), box)
    assert (config.alert_email, config.alert_recipients) == (None, [])
    assert (config.uptime_reminder_minutes, config.uptime_alert_delay_minutes) == (30, 0)


def test_ui_values_win_over_env(store, settings, box):
    update(store, settings, box, sender="ui@x.com", default_recipients=["Noc@x.com"],
           reminder_minutes=15, alert_delay_minutes=0, **SMTP)
    config = load_alert_config(store, settings, box)
    assert config.alert_email == "ui@x.com"
    assert config.alert_recipients == ["noc@x.com"]
    assert (config.uptime_reminder_minutes, config.uptime_alert_delay_minutes) == (15, 0)
    assert config.transport is EmailTransport.SMTP
    assert config.smtp_username == "AKIAEXAMPLE"
    assert config.smtp_password == PASSWORD


def test_null_reverts_to_env(store, settings, box):
    update(store, settings, box, sender="ui@x.com", reminder_minutes=15)
    view = update(store, settings, box, sender=None, reminder_minutes=None)
    assert view["sender"] == "env-sender@x.com"
    assert view["reminder_minutes"] == 60
    assert view["saved"]["sender"] is False


def test_password_is_stored_encrypted(store, settings, box):
    update(store, settings, box, **SMTP)
    assert PASSWORD not in json.dumps(store.load().values)


@pytest.mark.parametrize("other_box", [None, SecretBox(Fernet.generate_key().decode())])
def test_unreadable_password_counts_as_not_set_and_warns_once(store, settings, box, other_box,
                                                               caplog):
    update(store, settings, box, **SMTP)
    with caplog.at_level(logging.WARNING, logger="ssl_monitor.settings"):
        assert load_alert_config(store, settings, other_box).smtp_password is None
        assert describe_alert_settings(store, settings, other_box)["smtp_password_set"] is False
    assert len(caplog.records) == 1
    assert PASSWORD not in caplog.text


# ── Admin view ──────────────────────────────────────────────────────────────


def test_describe_shape_and_flags(store, settings, box):
    view = update(store, settings, box, sender="ui@x.com", **SMTP)
    assert view["saved"] == {"sender": True, "default_recipients": False,
                             "reminder_minutes": False, "alert_delay_minutes": False}
    assert view["env_defaults"] == {"sender": "env-sender@x.com",
                                    "default_recipients": ["env-ops@x.com"],
                                    "reminder_minutes": 60, "alert_delay_minutes": 5}
    assert view["transport"] == "smtp"
    assert view["aws_region"] == "ap-south-1"
    assert view["smtp_password_set"] is True
    assert view["encryption_available"] is True
    assert (view["updated_at"], view["updated_by"]) == (NOW, "admin")
    assert "smtp_password" not in view
    assert PASSWORD not in json.dumps(view, default=str)


def test_describe_without_encryption_key(store, settings):
    view = describe_alert_settings(store, settings, None)
    assert view["encryption_available"] is False
    assert view["smtp_password_set"] is False
    assert view["updated_at"] is None


# ── Updating ────────────────────────────────────────────────────────────────


def test_password_absent_keeps_it_and_null_removes_it(store, settings, box):
    update(store, settings, box, **SMTP)
    update(store, settings, box, smtp_username="AKIANEW")
    assert load_alert_config(store, settings, box).smtp_password == PASSWORD
    update(store, settings, box, transport="iam_role", smtp_password=None)
    assert load_alert_config(store, settings, box).smtp_password is None


def test_recipients_are_lowercased_and_deduplicated(store, settings, box):
    view = update(store, settings, box, default_recipients=["A@x.com", "a@x.com ", "", "b@x.com"])
    assert view["default_recipients"] == ["a@x.com", "b@x.com"]


def test_empty_recipient_list_is_saved(store, settings, box):
    view = update(store, settings, box, default_recipients=[])
    assert view["default_recipients"] == []
    assert view["saved"]["default_recipients"] is True


def test_blank_text_removes_the_value(store, settings, box):
    update(store, settings, box, sender="ui@x.com")
    assert update(store, settings, box, sender="  ")["sender"] == "env-sender@x.com"


def test_smtp_host_is_normalised(store, settings, box):
    view = update(store, settings, box, smtp_host=" Email-SMTP.eu-west-1.amazonaws.com ")
    assert view["smtp_host"] == "email-smtp.eu-west-1.amazonaws.com"


def test_empty_update_saves_nothing(store, settings, box):
    view = update(store, settings, box)
    assert view["updated_by"] is None


@pytest.mark.parametrize("changes", [
    {"sender": "not-an-email"},
    {"sender": 42},
    {"default_recipients": "a@x.com"},
    {"default_recipients": ["ok@x.com", "bad"]},
    {"reminder_minutes": 45},
    {"reminder_minutes": True},
    {"reminder_minutes": 30.0},
    {"alert_delay_minutes": 7},
    {"transport": "pigeon"},
    {"smtp_host": "smtp.gmail.com"},
    {"smtp_host": "email-smtp.us-east-1.amazonaws.com.evil.com"},
    {"smtp_port": 2525},
    {"smtp_password": ""},
    {"unknown": 1},
    {"transport": "smtp"},
    {"transport": "smtp", "smtp_host": SMTP["smtp_host"], "smtp_username": "AKIA"},
])
def test_invalid_changes_are_rejected_and_nothing_is_saved(store, settings, box, changes):
    with pytest.raises(InvalidInput):
        update(store, settings, box, **changes)
    assert store.load().values == {}


def test_removing_the_password_while_on_smtp_is_rejected(store, settings, box):
    update(store, settings, box, **SMTP)
    with pytest.raises(InvalidInput, match="SMTP password"):
        update(store, settings, box, smtp_password=None)


def test_switching_to_iam_role_and_removing_the_password_in_one_save(store, settings, box):
    """What the UI's "Remove password" sends while SMTP is the saved transport."""
    update(store, settings, box, **SMTP)
    described = update(store, settings, box, transport="iam_role", smtp_password=None)
    assert described["transport"] == "iam_role"
    assert described["smtp_password_set"] is False


def test_password_without_encryption_key_is_rejected(store, settings):
    with pytest.raises(InvalidInput, match="SETTINGS_ENCRYPTION_KEY") as caught:
        update(store, settings, None, smtp_password=PASSWORD)
    assert str(caught.value) == NO_ENCRYPTION_KEY


def test_saved_values_use_the_setting_keys(store, settings, box):
    update(store, settings, box, sender="ui@x.com", smtp_port=465)
    values = store.load().values
    assert values[SettingKey.ALERT_SENDER.value] == "ui@x.com"
    assert values[SettingKey.SMTP_PORT.value] == 465


# ── Test email ──────────────────────────────────────────────────────────────


class RefusingNotifier(ConsoleNotifier):
    def send_email(self, message):
        return False


def test_send_test_email(store, settings, box):
    notifier = ConsoleNotifier()
    config = load_alert_config(store, settings, box)
    assert send_test_email(notifier, config, " Me@x.com ") == "me@x.com"
    (message,) = notifier.sent_emails
    assert message.subject == TEST_EMAIL_SUBJECT and message.to == ("me@x.com",)


@pytest.mark.parametrize("to", ["", "nope", None])
def test_test_email_needs_a_valid_address(store, settings, box, to):
    with pytest.raises(InvalidInput):
        send_test_email(ConsoleNotifier(), load_alert_config(store, settings, box), to)


def test_test_email_needs_a_sender(store, box):
    config = load_alert_config(store, Settings.from_env(env={}), box)
    with pytest.raises(InvalidInput, match="sender"):
        send_test_email(ConsoleNotifier(), config, "me@x.com")


def test_test_email_refused(store, settings, box):
    with pytest.raises(EmailNotDelivered):
        send_test_email(RefusingNotifier(), load_alert_config(store, settings, box), "me@x.com")


def test_clearing_values_is_still_recorded_as_the_last_change():
    from datetime import datetime, timedelta, timezone

    from backend.ssl_monitor.adapters.storage.settings_memory import MemorySettingsStore
    from backend.ssl_monitor.config.settings import Settings
    from backend.ssl_monitor.services.app_settings import update_alert_settings

    store, settings = MemorySettingsStore(), Settings.from_env({})
    first = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    update_alert_settings(store, settings, None, {"reminder_minutes": 60}, user="alice", now=first)

    later = first + timedelta(hours=1)
    out = update_alert_settings(store, settings, None, {"reminder_minutes": None},
                                user="bob", now=later)

    assert out["updated_by"] == "bob"
    assert out["saved"]["reminder_minutes"] is False
