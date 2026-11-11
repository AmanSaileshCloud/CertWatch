"""Uptime alert models. Pure data — no I/O.

An alert is about one incident of one monitor: the first DOWN message, repeat
REMINDERs while it stays down, and one RECOVERED message. Uptime alerts go by
SES email only (SSL expiry alerts are unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .uptime_models import AlertChannel, Incident, Monitor

ALLOWED_REMINDER_MINUTES = (0, 15, 30, 60, 120)  # 0 = no reminders
ALLOWED_ALERT_DELAY_MINUTES = (0, 5, 10, 15, 30)  # 0 = alert as soon as DOWN is confirmed
RECENTLY_CLOSED_WINDOW_HOURS = 24  # closed incidents still considered for alerts


class AlertKind(str, Enum):
    DOWN = "down"
    REMINDER = "reminder"  # still down; repeats every reminder interval
    RECOVERED = "recovered"
    TEST = "test"


@dataclass(frozen=True)
class AlertTiming:
    """The effective per-monitor timing (monitor override, else the global default)."""

    alert_delay_minutes: int
    reminder_minutes: int


@dataclass(frozen=True)
class AlertCandidate:
    """An incident that may need an alert, with what is needed to decide and to write it."""

    incident: Incident
    monitor: Monitor
    customer_name: str | None = None
    group_name: str | None = None


@dataclass(frozen=True)
class AlertRule:
    """Extra alert recipient for one customer or one monitor (or global when both None)."""

    id: int
    target: str
    channel: AlertChannel = AlertChannel.EMAIL
    customer_id: int | None = None
    monitor_id: int | None = None
    on_down: bool = True  # also covers reminders
    on_recovery: bool = True
    enabled: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    text: str
    html: str
    to: tuple[str, ...]
