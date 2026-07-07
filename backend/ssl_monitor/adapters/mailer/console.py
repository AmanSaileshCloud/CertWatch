"""Console mailer — logs the digest instead of emailing it.

Lets you exercise the digest with zero email setup: it appears in the API log.
Keeps a record of what it 'sent' for tests.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("ssl_monitor.mailer")


class ConsoleMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_digest(self, email: str, domains: list) -> None:
        lines = [f"  {d.host}:{d.port}  {d.status.value}  days_remaining={d.days_remaining}" for d in domains]
        logger.warning(
            "DIGEST → %s\n%s",
            email,
            "\n".join(lines) if lines else "  (no domains monitored)",
        )
