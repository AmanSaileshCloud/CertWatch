"""Console notifier — the default local notifier.

Emits a single structured log line per alert/email so you can test the full
alerting path with no AWS account. Also keeps an in-process record of what was
sent, which the tests assert against.
"""

from __future__ import annotations

import logging

from ...core.alert_models import EmailMessage
from ...core.models import Alert
from .base import format_body, format_subject

logger = logging.getLogger("ssl_monitor.notifier")


class ConsoleNotifier:
    def __init__(self) -> None:
        self.sent: list[Alert] = []
        self.sent_emails: list[EmailMessage] = []

    def notify(self, alert: Alert) -> None:
        self.sent.append(alert)
        logger.warning(
            "ALERT %s | %s",
            format_subject(alert),
            format_body(alert),
        )

    def send_email(self, message: EmailMessage) -> bool:
        """Always accepted (True), like a provider taking every recipient."""
        self.sent_emails.append(message)
        logger.warning("EMAIL %s → %s", message.subject, ", ".join(message.to))
        return True
