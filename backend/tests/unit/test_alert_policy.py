"""Pure alert-timing rules (core/alert_policy.py)."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from backend.ssl_monitor.core.alert_models import AlertCandidate, AlertKind, AlertTiming
from backend.ssl_monitor.core.alert_policy import due_alert, effective_timing
from backend.ssl_monitor.core.uptime_models import CloseReason, Incident, Monitor

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MONITOR = Monitor(id=1, name="Shop", url="https://shop.test", interval_seconds=60)


def _at(minutes: float) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def _due(incident: Incident, now_minutes: float, *, delay: int = 0, reminder: int = 30,
         monitor: Monitor = MONITOR) -> AlertKind | None:
    timing = AlertTiming(alert_delay_minutes=delay, reminder_minutes=reminder)
    return due_alert(AlertCandidate(incident=incident, monitor=monitor), timing, _at(now_minutes))


OPEN = Incident(id=1, monitor_id=1, started_at=T0)


def _closed(minutes: float, reason: CloseReason = CloseReason.RECOVERED, **fields) -> Incident:
    return Incident(id=1, monitor_id=1, started_at=T0, resolved_at=_at(minutes),
                    duration_seconds=int(minutes * 60), close_reason=reason, **fields)


# ── effective_timing ─────────────────────────────────────────────


def test_effective_timing_uses_defaults_when_unset():
    timing = effective_timing(MONITOR, default_reminder_minutes=30, default_alert_delay_minutes=5)
    assert timing == AlertTiming(alert_delay_minutes=5, reminder_minutes=30)


def test_effective_timing_prefers_monitor_overrides_including_zero():
    monitor = replace(MONITOR, reminder_minutes=0, alert_delay_minutes=0)
    timing = effective_timing(monitor, default_reminder_minutes=30, default_alert_delay_minutes=5)
    assert timing == AlertTiming(alert_delay_minutes=0, reminder_minutes=0)


def test_effective_timing_mixed():
    monitor = replace(MONITOR, reminder_minutes=120)
    timing = effective_timing(monitor, default_reminder_minutes=30, default_alert_delay_minutes=10)
    assert timing == AlertTiming(alert_delay_minutes=10, reminder_minutes=120)


# ── Open incident, DOWN not sent ─────────────────────────────────


def test_open_with_no_delay_is_down_at_once():
    assert _due(OPEN, 0, delay=0) is AlertKind.DOWN


@pytest.mark.parametrize("minutes, expected", [
    (4.99, None),
    (5, AlertKind.DOWN),  # exactly the delay
    (6, AlertKind.DOWN),
])
def test_open_waits_for_the_delay(minutes, expected):
    assert _due(OPEN, minutes, delay=5) is expected


def test_alerts_disabled_is_never_due():
    monitor = replace(MONITOR, alerts_enabled=False)
    assert _due(OPEN, 60, monitor=monitor) is None
    assert _due(_closed(10, down_alert_sent_at=_at(0)), 60, monitor=monitor) is None


# ── Open incident, DOWN sent → reminders ─────────────────────────


DOWN_SENT = replace(OPEN, down_alert_sent_at=_at(1))


@pytest.mark.parametrize("minutes, expected", [
    (1, None),
    (30.99, None),
    (31, AlertKind.REMINDER),  # exactly reminder after the DOWN alert
    (90, AlertKind.REMINDER),
])
def test_first_reminder_counts_from_the_down_alert(minutes, expected):
    assert _due(DOWN_SENT, minutes, reminder=30) is expected


@pytest.mark.parametrize("minutes, expected", [
    (31, None),
    (45.99, None),
    (46, AlertKind.REMINDER),  # exactly 15 min after the last reminder
])
def test_next_reminder_counts_from_the_last_reminder(minutes, expected):
    incident = replace(DOWN_SENT, last_reminder_sent_at=_at(31), reminder_count=1)
    assert _due(incident, minutes, reminder=15) is expected


def test_reminder_zero_means_never():
    assert _due(DOWN_SENT, 10_000, reminder=0) is None


def test_delay_does_not_affect_reminders():
    assert _due(DOWN_SENT, 31, delay=30, reminder=30) is AlertKind.REMINDER


# ── Closed incidents ─────────────────────────────────────────────


@pytest.mark.parametrize("reason", [CloseReason.PAUSED, CloseReason.EDITED])
@pytest.mark.parametrize("down_sent", [None, T0])
def test_paused_or_edited_closure_never_alerts(reason, down_sent):
    assert _due(_closed(60, reason, down_alert_sent_at=down_sent), 61) is None


def test_closure_without_reason_never_alerts():
    incident = replace(_closed(60, down_alert_sent_at=T0), close_reason=None)
    assert _due(incident, 61) is None


def test_recovered_after_down_sent_is_recovered():
    assert _due(_closed(10, down_alert_sent_at=T0), 11) is AlertKind.RECOVERED


def test_recovered_ignores_reminder_timing():
    incident = _closed(10, down_alert_sent_at=T0, last_reminder_sent_at=_at(9))
    assert _due(incident, 10, reminder=30) is AlertKind.RECOVERED


def test_recovery_already_sent_is_done():
    incident = _closed(10, down_alert_sent_at=T0, recovery_alert_sent_at=_at(10))
    assert _due(incident, 11) is None


@pytest.mark.parametrize("outage_minutes, expected", [
    (4, None),  # blip shorter than the delay: silent
    (5, AlertKind.DOWN),  # exactly the delay
    (20, AlertKind.DOWN),
])
def test_recovered_before_down_sent_depends_on_the_delay(outage_minutes, expected):
    assert _due(_closed(outage_minutes), 60, delay=5) is expected


def test_quick_flap_with_no_delay_still_sends_down():
    assert _due(_closed(0), 1, delay=0) is AlertKind.DOWN


def test_missing_duration_falls_back_to_the_timestamps():
    incident = replace(_closed(5), duration_seconds=None)
    assert _due(incident, 60, delay=5) is AlertKind.DOWN
    assert _due(incident, 60, delay=10) is None


# ── Alerts switched back on after an outage ──────────────────────


def test_outage_that_ended_before_alerts_returned_is_silent():
    monitor = replace(MONITOR, alerts_enabled_at=_at(60))
    assert _due(_closed(10), 61, monitor=monitor) is None


def test_outage_that_ended_after_alerts_returned_still_gets_down():
    monitor = replace(MONITOR, alerts_enabled_at=_at(5))
    assert _due(_closed(10), 11, monitor=monitor) is AlertKind.DOWN


def test_recovered_still_follows_a_down_sent_before_alerts_were_off():
    monitor = replace(MONITOR, alerts_enabled_at=_at(60))
    incident = _closed(10, down_alert_sent_at=_at(1))
    assert _due(incident, 61, monitor=monitor) is AlertKind.RECOVERED
