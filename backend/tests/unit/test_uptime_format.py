"""Unit tests for the uptime alert email formatter."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.notifier.uptime_format import (
    format_duration,
    format_duration_long,
    format_uptime_alert,
)
from backend.ssl_monitor.core.alert_models import AlertCandidate, AlertKind, AlertReport
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
    assert msg.text.startswith("Shop - Monitor is Down\nDown since 11-Nov-2026, 02:02:00 pm UTC\n")
    for expected in ("Site monitored: https://shop.example.com",
                     "Customer / Group: Acme Corp · E-commerce",
                     "Down Duration: 1 Hrs 30 Mins", "Reason(s): HTTP 503",
                     "Last Status Code: 503",
                     "Check Settings: Every 5m · expects HTTP 200 · timeout 10s"):
        assert expected in msg.text
    assert "Shop - Monitor is Down" in msg.html
    assert "#e5484d" in msg.html  # red banner
    assert "Reminder" not in msg.text


def test_reminder_subject_uses_time_since_start():
    msg = _format(AlertKind.REMINDER, _candidate(reminder_count=2))
    assert msg.subject == "[STILL DOWN 1h 30m] Acme Corp · E-commerce · Shop"
    assert "Shop - Monitor is Still Down" in msg.text
    assert "Down Duration: 1 Hrs 30 Mins" in msg.text
    assert "Reminder: #3" in msg.text
    assert "#e5484d" in msg.html


def test_recovered_subject_uses_incident_duration():
    msg = _format(AlertKind.RECOVERED, _candidate(resolved=True))
    assert msg.subject == "[RECOVERED after 9m 40s] Acme Corp · E-commerce · Shop"
    assert "Shop - Monitor is Available" in msg.text
    assert "Available since 11-Nov-2026, 02:11:40 pm UTC" in msg.text
    assert "Last Down Duration: 9 Mins 40 Secs" in msg.text
    assert "Reason(s): HTTP 503 (Resolved)" in msg.text
    assert "#2ecc71" in msg.html  # green banner


def test_test_subject():
    msg = _format(AlertKind.TEST)
    assert msg.subject == "[TEST] Acme Corp · E-commerce · Shop"
    assert "Shop - Test Alert" in msg.html
    assert "#4733e0" in msg.html


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 Mins 0 Secs"), (41, "0 Mins 41 Secs"), (5400, "1 Hrs 30 Mins"),
     (183600, "2 Days 3 Hrs")],
)
def test_format_duration_long(seconds, expected):
    assert format_duration_long(seconds) == expected


def test_report_sections_and_ip():
    report = AlertReport(
        resolved_ip="3.6.142.81", availability_pct=99.981, downtimes=1, downtime_seconds=41,
        checks=288, uptime_pct=100.0, avg_response_ms=212, min_response_ms=150,
        max_response_ms=480,
    )
    msg = format_uptime_alert(AlertKind.DOWN, _candidate(), now=NOW, to=TO, public_url=None,
                              report=report)
    for expected in ("Resolved IP: 3.6.142.81", "Availability Summary Report - Last 3 days",
                     "Availability: 99.981 %", "No. of Downtimes: 1",
                     "Downtime Duration: 0 Mins 41 Secs", "Performance Report - Last 24 Hours",
                     "Avg. Response Time: 212 ms", "Fastest: 150 ms", "Slowest: 480 ms"):
        assert expected in msg.text
    assert "Availability Summary Report" in msg.html
    assert "99.981 %" in msg.html


def test_no_report_sections_without_report():
    msg = _format(AlertKind.DOWN)
    assert "Availability Summary" not in msg.text
    assert "Resolved IP" not in msg.text


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


def test_times_shown_in_report_timezone():
    msg = format_uptime_alert(
        AlertKind.RECOVERED, _candidate(resolved=True), now=NOW, to=TO, public_url=None,
        tz="Asia/Kolkata",
    )
    assert "Available since 11-Nov-2026, 07:41:40 pm IST" in msg.text  # 14:11 UTC + 5:30
    assert "Available since 11-Nov-2026, 07:41:40 pm IST" in msg.html
    assert "UTC" not in msg.text


def _series_report() -> AlertReport:
    from backend.ssl_monitor.core.uptime_models import CheckResult

    series = tuple(
        CheckResult(monitor_id=42, checked_at=NOW - dt.timedelta(minutes=m), is_up=True,
                    response_ms=700 + m)
        for m in range(0, 120, 5)
    )
    return AlertReport(checks=len(series), response_series=series)


@pytest.mark.parametrize("kind", list(AlertKind))
def test_performance_report_includes_the_chart(kind):
    msg = format_uptime_alert(kind, _candidate(), now=NOW, to=TO, public_url=None,
                              report=_series_report())
    assert len(msg.images) == 1
    image = msg.images[0]
    assert image.data.startswith(b"\x89PNG")
    assert f'src="cid:{image.cid}"' in msg.html
    assert msg.html.index("Performance Report") < msg.html.index(f"cid:{image.cid}")


def test_no_chart_without_checks():
    msg = format_uptime_alert(AlertKind.DOWN, _candidate(), now=NOW, to=TO, public_url=None,
                              report=AlertReport())
    assert msg.images == ()
    assert "cid:" not in msg.html
