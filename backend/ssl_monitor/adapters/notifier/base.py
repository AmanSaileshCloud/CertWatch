"""Notifier port and a shared message formatter.

The formatter is pure and reused by every notifier so the console log line and
the SNS/SES body stay identical.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...core.models import Alert


@runtime_checkable
class NotifierPort(Protocol):
    def notify(self, alert: Alert) -> None:
        """Deliver an alert. Implementations must not raise on delivery failure
        in a way that aborts a checker run — log and continue."""
        ...


def format_subject(alert: Alert) -> str:
    return f"[SSL {alert.status.value.upper()}] {alert.domain}"


def format_body(alert: Alert) -> str:
    parts = [
        f"domain={alert.domain}",
        f"status={alert.status.value}",
        f"days_remaining={alert.days_remaining}",
        f"threshold={alert.threshold}",
    ]
    if alert.not_after is not None:
        parts.append(f"not_after={alert.not_after.isoformat()}")
    return " ".join(parts)
