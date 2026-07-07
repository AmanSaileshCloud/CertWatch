"""Mailer port — how the daily digest email leaves the system.

Console for local dev; swap in SMTP or SES without touching callers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MailerPort(Protocol):
    def send_digest(self, email: str, domains: list) -> None:
        """Deliver a daily certificate status digest to ``email``."""
        ...
