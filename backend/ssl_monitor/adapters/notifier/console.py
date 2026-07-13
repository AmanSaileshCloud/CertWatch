"""Console notifier — the default local notifier.

Emits a single structured log line per alert so you can test the full alerting
path with no AWS account. Also keeps an in-process record of sent alerts, which
the integration test asserts against.
"""

from __future__ import annotations

import logging

from ...core.models import Alert
from .base import format_body, format_subject

logger = logging.getLogger("ssl_monitor.notifier")


class ConsoleNotifier:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def notify(self, alert: Alert) -> None:
        self.sent.append(alert)
        logger.warning(
            "ALERT %s | %s",
            format_subject(alert),
            format_body(alert),
        )
