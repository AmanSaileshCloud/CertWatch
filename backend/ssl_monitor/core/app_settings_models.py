"""Settings editable from the admin UI. Pure data — no I/O.

Resolution order for every value: saved in the UI (app_settings table) → environment
variable → built-in default. The SES SMTP password is only ever held encrypted at
rest and is never returned by the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

SES_SMTP_HOST_PATTERN = r"^email-smtp\.[a-z0-9-]+\.amazonaws\.com$"
ALLOWED_SMTP_PORTS = (25, 465, 587, 2465, 2587)
DEFAULT_SMTP_PORT = 587


class EmailTransport(str, Enum):
    IAM_ROLE = "iam_role"  # boto3 SES API with the instance role / env credentials
    SMTP = "smtp"  # SES SMTP interface with username + password saved in the UI


class SettingKey(str, Enum):
    """Keys of the app_settings table (values are JSON)."""

    ALERT_SENDER = "alerts.sender"
    ALERT_DEFAULT_RECIPIENTS = "alerts.default_recipients"
    ALERT_REMINDER_MINUTES = "alerts.reminder_minutes"
    ALERT_DELAY_MINUTES = "alerts.alert_delay_minutes"
    EMAIL_TRANSPORT = "email.transport"
    SMTP_HOST = "email.smtp_host"
    SMTP_PORT = "email.smtp_port"
    SMTP_USERNAME = "email.smtp_username"
    SMTP_PASSWORD_ENCRYPTED = "email.smtp_password_encrypted"


@dataclass(frozen=True)
class StoredSettings:
    """Raw saved values (absent keys = not set in the UI) plus audit info."""

    values: dict[str, object] = field(default_factory=dict)
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class AlertConfig:
    """Effective alert/email configuration (UI value, else env, else default).

    Attribute names match what the alert service already reads from Settings
    (alert_email, alert_recipients, uptime_*_minutes, public_url), so either can be
    passed to it."""

    alert_email: str | None
    alert_recipients: list[str]
    uptime_reminder_minutes: int
    uptime_alert_delay_minutes: int
    public_url: str | None
    transport: EmailTransport
    aws_region: str
    smtp_host: str | None = None
    smtp_port: int = DEFAULT_SMTP_PORT
    smtp_username: str | None = None
    # Decrypted; repr=False keeps it out of logs, tracebacks and test diffs.
    smtp_password: str | None = field(default=None, repr=False)
    # Time zone for timestamps in alert emails (REPORT_TIMEZONE).
    report_timezone: str = "UTC"
