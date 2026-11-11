"""Unit tests for the uptime alert email formatter."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.notifier.uptime_format import (
    format_duration,
    format_uptime_alert,
)
from backend.ssl_monitor.core.alert_models import AlertCandidate, AlertKind
from backend.ssl_monitor.core.uptime_models import Incident, Monitor

UTC = dt.timezone.utc
STARTED = dt.datetime(2026, 11, 11, 14, 2, tzinfo=UTC)
NOW = STARTED + dt.timedelta(hours=1, minutes=30)
TO = ("ops@example.com", "dev@example.com")


def _candidate(
    *,
    name: str = "Shop",
    url: str = "https://shop.example.com",
    reason: str | None = "HTTP 503",
    resolved: bool = False,
    customer: str | None = "Acme Corp",
    group: str | None = "E-commerce",
    reminder_count: int = 0,
) -> AlertCandidate:
    monitor = Monitor(
        id=42, name=name, url=url, interval_seconds=300, last_status_code=503,
    )
    incident = Incident(
        id=7,
        monitor_id=42,
        started_at=STARTED,
        resolved_at=STARTED + dt.timedelta(minutes=9, seconds=40) if resolved else None,
        duration_seconds=580 if resolved else None,
        reason=reason,
        reminder_count=reminder_count,
    )
    return AlertCandidate(incident=incident, monitor=monitor, customer_name=customer, group_name=group)


def _format(kind: AlertKind, candidate: AlertCandidate | None = None, public_url: str | None = None):
    return format_uptime_alert(
        kind, candidate or _candidate(), now=NOW, to=TO, public_url=public_url
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (45, "45s"), (60, "1m"), (580, "9m 40s"), (5400, "1h 30m"),
     (3600, "1h"), (183600, "2d 3h"), (-5, "0s")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_down_subject_and_body():
    msg = _format(AlertKind.DOWN)
    assert msg.subject == "[DOWN] Acme Corp · E-commerce · Shop"
    assert msg.to == TO
    for expected in ("Shop", "https://shop.example.com", "Status: DOWN",
                     "11 Nov 2026, 14:02 UTC", "HTTP 503", "503", "every 5m"):
        assert expected in msg.text
    assert "#991b1b" in msg.html  # red card
    assert "Reminder" not in msg.text


def test_reminder_subject_uses_time_since_start():
    msg = _format(AlertKind.REMINDER, _candidate(reminder_count=2))
    assert msg.subject == "[STILL DOWN 1h 30m] Acme Corp · E-commerce · Shop"
    assert "Duration: 1h 30m" in msg.text
    assert "Reminder: #3" in msg.text
    assert "#991b1b" in msg.html


def test_recovered_subject_uses_incident_duration():
    msg = _format(AlertKind.RECOVERED, _candidate(resolved=True))
    assert msg.subject == "[RECOVERED after 9m 40s] Acme Corp · E-commerce · Shop"
    assert "Status: UP" in msg.text
    assert "Recovered at: 11 Nov 2026, 14:11 UTC" in msg.text
    assert "#166534" in msg.html  # green card


def test_test_subject():
    msg = _format(AlertKind.TEST)
    assert msg.subject == "[TEST] Acme Corp · E-commerce · Shop"
    assert "#3730a3" in msg.html


@pytest.mark.parametrize(
    ("customer", "group", "title"),
    [(None, None, "Shop"), ("Acme Corp", None, "Acme Corp · Shop"), (None, "E-commerce", "E-commerce · Shop")],
)
def test_missing_customer_or_group_is_omitted(customer, group, title):
    msg = _format(AlertKind.DOWN, _candidate(customer=customer, group=group))
    assert msg.subject == f"[DOWN] {title}"


def test_names_never_break_the_subject_onto_another_line():
    candidate = _candidate(name="Shop\r\nBcc: evil@x.com", customer="Acme\x00 \t Corp",
                           group="\x1b\x7f")
    subject = _format(AlertKind.DOWN, candidate).subject
    assert subject == "[DOWN] Acme Corp · Shop Bcc: evil@x.com"
    assert all(ord(char) >= 32 and ord(char) != 127 for char in subject)


def test_dashboard_link_when_public_url_set():
    msg = _format(AlertKind.DOWN, public_url="https://certwatch.example.com/")
    link = "https://certwatch.example.com/#/websites/42"
    assert f"View in CertWatch: {link}" in msg.text
    assert f'href="{link}"' in msg.html


def test_no_dashboard_link_without_public_url():
    msg = _format(AlertKind.DOWN)
    assert "View in CertWatch" not in msg.text
    assert "View in CertWatch" not in msg.html


def test_html_escapes_user_controlled_values():
    msg = _format(
        AlertKind.DOWN,
        _candidate(
            name="<script>alert(1)</script>",
            url='https://evil.example.com/"><img src=x>',
            reason="<b>boom</b> & more",
            customer="Tom & Jerry",
        ),
    )
    assert "<script>" not in msg.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg.html
    assert "<img" not in msg.html
    assert "<b>boom</b>" not in msg.html
    assert "&lt;b&gt;boom&lt;/b&gt; &amp; more" in msg.html
    assert "Tom &amp; Jerry" in msg.html
    # The plain-text part is not HTML, so it keeps the raw value.
    assert "<script>alert(1)</script>" in msg.text


def test_non_http_url_is_not_linked():
    msg = _format(AlertKind.DOWN, _candidate(url="javascript:alert(1)"))
    assert 'href="javascript:' not in msg.html
