"""Alert and email-delivery settings edited on the admin Settings page.

Every value resolves as: saved in the UI → environment variable → built-in default.
The SES SMTP password is stored encrypted and never leaves this module in clear text
except inside :class:`AlertConfig` (which is never serialised).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import lru_cache, partial
from typing import Any

from ..adapters.notifier.base import NotifierPort
from ..adapters.secret_box import SecretBox, SecretBoxError
from ..adapters.storage.base import SettingsStorePort
from ..config.settings import Settings
from ..core.alert_models import ALLOWED_ALERT_DELAY_MINUTES, ALLOWED_REMINDER_MINUTES, EmailMessage
from ..core.app_settings_models import (
    ALLOWED_SMTP_PORTS,
    DEFAULT_SMTP_PORT,
    SES_SMTP_HOST_PATTERN,
    AlertConfig,
    EmailTransport,
    SettingKey,
    StoredSettings,
)
from .alert_rules import is_valid_email
from .errors import InvalidSetting, ServiceError

logger = logging.getLogger("ssl_monitor.settings")

NO_ENCRYPTION_KEY = "Set SETTINGS_ENCRYPTION_KEY in the server environment to store SMTP credentials"
TEST_EMAIL_SUBJECT = "[TEST] CertWatch email delivery"

# API field → the app_settings key it is saved under (all but the SMTP fields fall
# back to an environment variable).
_ALERT_FIELDS = {
    "sender": SettingKey.ALERT_SENDER,
    "default_recipients": SettingKey.ALERT_DEFAULT_RECIPIENTS,
    "reminder_minutes": SettingKey.ALERT_REMINDER_MINUTES,
    "alert_delay_minutes": SettingKey.ALERT_DELAY_MINUTES,
}
_SMTP_REQUIRED = {
    SettingKey.SMTP_HOST: "SMTP host",
    SettingKey.SMTP_USERNAME: "SMTP username",
    SettingKey.SMTP_PASSWORD_ENCRYPTED: "SMTP password",
}


class EmailNotDelivered(ServiceError):
    """The email provider did not accept the test email."""


# ── Reading ─────────────────────────────────────────────────────────────────


def load_alert_config(
    store: SettingsStorePort, settings: Settings, box: SecretBox | None
) -> AlertConfig:
    return _alert_config(store.load(), settings, box)


def describe_alert_settings(
    store: SettingsStorePort, settings: Settings, box: SecretBox | None
) -> dict[str, Any]:
    """The admin view (AlertSettingsAdminOut). Never includes the password."""
    stored = store.load()
    config = _alert_config(stored, settings, box)
    return {
        "sender": config.alert_email,
        "default_recipients": config.alert_recipients,
        "reminder_minutes": config.uptime_reminder_minutes,
        "alert_delay_minutes": config.uptime_alert_delay_minutes,
        "saved": {name: key.value in stored.values for name, key in _ALERT_FIELDS.items()},
        "env_defaults": {
            "sender": settings.alert_email,
            "default_recipients": list(settings.alert_recipients),
            "reminder_minutes": settings.uptime_reminder_minutes,
            "alert_delay_minutes": settings.uptime_alert_delay_minutes,
        },
        "transport": config.transport.value,
        "aws_region": config.aws_region,
        "smtp_host": config.smtp_host,
        "smtp_port": config.smtp_port,
        "smtp_username": config.smtp_username,
        "smtp_password_set": config.smtp_password is not None,
        "encryption_available": box is not None,
        "updated_at": stored.updated_at,
        "updated_by": stored.updated_by,
    }


def _alert_config(stored: StoredSettings, settings: Settings, box: SecretBox | None) -> AlertConfig:
    values = stored.values

    def saved(key: SettingKey, default: Any) -> Any:
        return values.get(key.value, default)

    return AlertConfig(
        alert_email=saved(SettingKey.ALERT_SENDER, settings.alert_email),
        alert_recipients=list(saved(SettingKey.ALERT_DEFAULT_RECIPIENTS, settings.alert_recipients)),
        uptime_reminder_minutes=saved(SettingKey.ALERT_REMINDER_MINUTES,
                                      settings.uptime_reminder_minutes),
        uptime_alert_delay_minutes=saved(SettingKey.ALERT_DELAY_MINUTES,
                                         settings.uptime_alert_delay_minutes),
        public_url=settings.public_url,
        transport=EmailTransport(saved(SettingKey.EMAIL_TRANSPORT, EmailTransport.IAM_ROLE.value)),
        aws_region=settings.aws_region,
        smtp_host=saved(SettingKey.SMTP_HOST, None),
        smtp_port=saved(SettingKey.SMTP_PORT, DEFAULT_SMTP_PORT),
        smtp_username=saved(SettingKey.SMTP_USERNAME, None),
        smtp_password=_decrypt_password(saved(SettingKey.SMTP_PASSWORD_ENCRYPTED, None), box),
        report_timezone=settings.report_timezone,
    )


def _decrypt_password(token: str | None, box: SecretBox | None) -> str | None:
    """The saved password, or None (warned once) when it cannot be read."""
    if token is None:
        return None
    if box is None:
        _warn_once("An SMTP password is saved but SETTINGS_ENCRYPTION_KEY is missing or "
                   "invalid, so it is ignored")
        return None
    try:
        return box.decrypt(token)
    except SecretBoxError:
        _warn_once("The saved SMTP password cannot be decrypted with SETTINGS_ENCRYPTION_KEY "
                   "(was the key changed?) — re-enter it on the Settings page")
        return None


@lru_cache(maxsize=None)
def _warn_once(message: str) -> None:
    logger.warning(message)


# ── Updating ────────────────────────────────────────────────────────────────


LAST_CHANGE_KEY = "meta.last_change"


def update_alert_settings(
    store: SettingsStorePort,
    settings: Settings,
    box: SecretBox | None,
    changes: Mapping[str, Any],
    *,
    user: str,
    now: datetime,
) -> dict[str, Any]:
    """Validate and save a partial update. ``None`` removes a saved value (back to the
    environment / default); for ``smtp_password`` a string replaces it and an absent
    key leaves it unchanged. Raises InvalidSetting (422) on any bad value."""
    cleaned = _clean_changes(changes, box)
    _require_complete_smtp({**store.load().values, **cleaned})
    if cleaned:
        # The audit row is always written, so a change that only clears values
        # (reset to server default) still shows up in "Last changed by".
        store.save({**cleaned, LAST_CHANGE_KEY: now.isoformat()}, updated_by=user, now=now)
    return describe_alert_settings(store, settings, box)


def _clean_changes(changes: Mapping[str, Any], box: SecretBox | None) -> dict[str, Any]:
    fields = _field_cleaners(box)
    cleaned: dict[str, Any] = {}
    for name, value in changes.items():
        if name not in fields:
            raise InvalidSetting(f"Unknown setting: {name}")
        key, clean = fields[name]
        cleaned[key.value] = None if value is None else clean(value)
    return cleaned


Cleaner = Callable[[Any], Any]


def _field_cleaners(box: SecretBox | None) -> dict[str, tuple[SettingKey, Cleaner]]:
    return {
        "sender": (SettingKey.ALERT_SENDER, _clean_sender),
        "default_recipients": (SettingKey.ALERT_DEFAULT_RECIPIENTS, _clean_recipients),
        "reminder_minutes": (SettingKey.ALERT_REMINDER_MINUTES,
                             partial(_one_of, ALLOWED_REMINDER_MINUTES, "Reminder interval")),
        "alert_delay_minutes": (SettingKey.ALERT_DELAY_MINUTES,
                                partial(_one_of, ALLOWED_ALERT_DELAY_MINUTES, "Alert delay")),
        "transport": (SettingKey.EMAIL_TRANSPORT, _clean_transport),
        "smtp_host": (SettingKey.SMTP_HOST, _clean_smtp_host),
        "smtp_port": (SettingKey.SMTP_PORT, partial(_one_of, ALLOWED_SMTP_PORTS, "SMTP port")),
        "smtp_username": (SettingKey.SMTP_USERNAME, partial(_text, "SMTP username")),
        "smtp_password": (SettingKey.SMTP_PASSWORD_ENCRYPTED, partial(_encrypt_password, box)),
    }


def _text(name: str, value: Any) -> str | None:
    """Trimmed text; blank means "remove" (None)."""
    if not isinstance(value, str):
        raise InvalidSetting(f"{name} must be text")
    return value.strip() or None


def _email(name: str, value: Any) -> str | None:
    address = _text(name, value)
    if address is not None and not is_valid_email(address):
        raise InvalidSetting(f"{name} is not a valid email address: {address}")
    return address


def _clean_sender(value: Any) -> str | None:
    return _email("Sender", value)


def _clean_recipients(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise InvalidSetting("Default recipients must be a list of email addresses")
    addresses = (_email("Recipient", item) for item in value)
    return list(dict.fromkeys(a.lower() for a in addresses if a))


def _one_of(allowed: tuple[int, ...], name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in allowed:
        raise InvalidSetting(f"{name} must be one of {', '.join(map(str, allowed))}")
    return value


def _clean_transport(value: Any) -> str:
    try:
        return EmailTransport(value).value
    except ValueError:
        options = ", ".join(t.value for t in EmailTransport)
        raise InvalidSetting(f"Email transport must be one of {options}") from None


def _clean_smtp_host(value: Any) -> str | None:
    host = _text("SMTP host", value)
    if host is not None and not re.fullmatch(SES_SMTP_HOST_PATTERN, host.lower()):
        raise InvalidSetting("SMTP host must be an Amazon SES SMTP endpoint, "
                             "e.g. email-smtp.us-east-1.amazonaws.com")
    return host.lower() if host else None


def _encrypt_password(box: SecretBox | None, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSetting("SMTP password must not be empty")
    if box is None:
        raise InvalidSetting(NO_ENCRYPTION_KEY)
    return box.encrypt(value)


def _require_complete_smtp(values: Mapping[str, Any]) -> None:
    """SMTP delivery needs host, username and password once the change is applied."""
    if values.get(SettingKey.EMAIL_TRANSPORT.value) != EmailTransport.SMTP.value:
        return
    saved = {key for key, value in values.items() if value is not None}
    missing = [label for key, label in _SMTP_REQUIRED.items() if key.value not in saved]
    if missing:
        raise InvalidSetting(f"SES SMTP delivery needs: {', '.join(missing)}")


# ── Test email ──────────────────────────────────────────────────────────────


def send_test_email(notifier: NotifierPort, config: AlertConfig, to: Any) -> str:
    """Send the test email to one address and return it. Raises InvalidSetting (bad
    address, no sender) or EmailNotDelivered (the provider refused it)."""
    address = (_email("Test recipient", to) or "").lower()
    if not address:
        raise InvalidSetting("Enter the email address to send the test to")
    if not config.alert_email:
        raise InvalidSetting("Set a sender address before sending a test email")
    if not notifier.send_email(_test_message(address)):
        raise EmailNotDelivered(f"The test email to {address} was not accepted")
    return address


def _test_message(to: str) -> EmailMessage:
    return EmailMessage(
        subject=TEST_EMAIL_SUBJECT,
        text="This is a test email from CertWatch. If you can read it, alert emails "
             "will be delivered.",
        html="<p>This is a test email from <strong>CertWatch</strong>.</p>"
             "<p>If you can read it, alert emails will be delivered.</p>",
        to=(to,),
    )
