"""Request/response models for the admin Settings page (/settings/alerts)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..core.app_settings_models import EmailTransport


class SavedFlags(BaseModel):
    """True = the value was saved in the UI (else it comes from env / default)."""

    sender: bool
    default_recipients: bool
    reminder_minutes: bool
    alert_delay_minutes: bool


class EnvDefaults(BaseModel):
    sender: str | None
    default_recipients: list[str]
    reminder_minutes: int
    alert_delay_minutes: int


class AlertSettingsAdminOut(BaseModel):
    sender: str | None
    default_recipients: list[str]
    reminder_minutes: int
    alert_delay_minutes: int
    saved: SavedFlags
    env_defaults: EnvDefaults
    transport: EmailTransport
    aws_region: str
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password_set: bool
    encryption_available: bool
    updated_at: datetime | None
    updated_by: str | None


class AlertSettingsIn(BaseModel):
    """Any subset of fields; null removes a saved value. ``smtp_password``: a string
    sets it, null removes it, absent leaves it unchanged (it is never returned)."""

    model_config = ConfigDict(extra="forbid")

    sender: str | None = None
    default_recipients: list[str] | None = None
    reminder_minutes: int | None = None
    alert_delay_minutes: int | None = None
    transport: EmailTransport | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None


class SendTestEmailIn(BaseModel):
    to: str
