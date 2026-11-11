"""When is an uptime alert due? Pure rules — no I/O, no clock reads.

For one incident (open, or recently closed with the recovery not yet sent):

* open, DOWN not sent      → DOWN once it has lasted ``alert_delay_minutes``;
* open, DOWN sent          → REMINDER every ``reminder_minutes`` (0 = never), counted
                             from the last reminder, else from the DOWN alert;
* closed paused / edited   → nothing, ever;
* closed before alerts were switched back on, DOWN never sent → nothing;
* closed recovered:
    - DOWN not sent        → DOWN if the outage lasted at least the delay (RECOVERED
                             follows once DOWN is marked sent), else nothing — a blip
                             shorter than the delay stays silent;
    - DOWN sent            → RECOVERED, once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .alert_models import AlertCandidate, AlertKind, AlertTiming
from .uptime_models import CloseReason, Incident, Monitor


def effective_timing(
    monitor: Monitor, *, default_reminder_minutes: int, default_alert_delay_minutes: int
) -> AlertTiming:
    """The monitor's own timing where set, else the global defaults."""
    return AlertTiming(
        alert_delay_minutes=_pick(monitor.alert_delay_minutes, default_alert_delay_minutes),
        reminder_minutes=_pick(monitor.reminder_minutes, default_reminder_minutes),
    )


def _pick(override: int | None, default: int) -> int:
    return default if override is None else override


def due_alert(candidate: AlertCandidate, timing: AlertTiming, now: datetime) -> AlertKind | None:
    """The alert to send now for this candidate, or None."""
    if not candidate.monitor.alerts_enabled:
        return None
    incident = candidate.incident
    if incident.is_open:
        return _due_while_open(incident, timing, now)
    if _ended_while_alerts_were_off(incident, candidate.monitor):
        return None
    return _due_after_close(incident, timing)


def _ended_while_alerts_were_off(incident: Incident, monitor: Monitor) -> bool:
    """An outage that ended before alerts were switched back on, with no DOWN sent,
    is history: a late DOWN + RECOVERED pair for it would only confuse."""
    enabled_at = monitor.alerts_enabled_at
    return (incident.down_alert_sent_at is None and enabled_at is not None
            and incident.resolved_at <= enabled_at)


def _due_while_open(incident: Incident, timing: AlertTiming, now: datetime) -> AlertKind | None:
    if incident.down_alert_sent_at is None:
        delay = timedelta(minutes=timing.alert_delay_minutes)
        return AlertKind.DOWN if now - incident.started_at >= delay else None
    if timing.reminder_minutes <= 0:
        return None
    last_sent = incident.last_reminder_sent_at or incident.down_alert_sent_at
    due = now - last_sent >= timedelta(minutes=timing.reminder_minutes)
    return AlertKind.REMINDER if due else None


def _due_after_close(incident: Incident, timing: AlertTiming) -> AlertKind | None:
    if incident.close_reason != CloseReason.RECOVERED or incident.recovery_alert_sent_at:
        return None
    if incident.down_alert_sent_at is not None:
        return AlertKind.RECOVERED
    if _outage_seconds(incident) >= timing.alert_delay_minutes * 60:
        return AlertKind.DOWN
    return None


def _outage_seconds(incident: Incident) -> float:
    if incident.duration_seconds is not None:
        return incident.duration_seconds
    return (incident.resolved_at - incident.started_at).total_seconds()
