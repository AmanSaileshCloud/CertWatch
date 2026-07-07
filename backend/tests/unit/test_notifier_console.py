"""Unit tests for the console notifier and the shared message formatter."""

from __future__ import annotations

import datetime as dt
import logging

from backend.ssl_monitor.adapters.notifier.base import (
    NotifierPort,
    format_body,
    format_subject,
)
from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.core.models import Alert, Status

UTC = dt.timezone.utc


def _alert() -> Alert:
    return Alert(
        domain="example.com:443",
        status=Status.WARNING,
        days_remaining=6,
        threshold=7,
        not_after=dt.datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC),
    )


def test_console_notifier_satisfies_port():
    assert isinstance(ConsoleNotifier(), NotifierPort)


def test_notify_records_and_logs(caplog):
    notifier = ConsoleNotifier()
    with caplog.at_level(logging.WARNING, logger="ssl_monitor.notifier"):
        notifier.notify(_alert())
    assert len(notifier.sent) == 1
    assert notifier.sent[0].domain == "example.com:443"
    assert "example.com:443" in caplog.text
    assert "ALERT" in caplog.text


def test_formatters():
    alert = _alert()
    assert format_subject(alert) == "[SSL WARNING] example.com:443"
    body = format_body(alert)
    assert "days_remaining=6" in body
    assert "threshold=7" in body
    assert "not_after=2026-07-02T12:00:00+00:00" in body
