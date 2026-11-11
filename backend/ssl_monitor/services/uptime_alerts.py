"""Uptime alert dispatch — runs after each uptime check cycle.

For every incident that may need an alert, the pure policy (core/alert_policy)
says which alert is due; this module finds the recipients, formats the email,
sends it and only then marks it sent (at-least-once: a failed send is retried once
its backoff has passed). A closed incident whose DOWN alert never went out gets
DOWN and then RECOVERED in the same cycle.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from ..adapters.notifier.base import NotifierPort
from ..adapters.notifier.uptime_format import format_uptime_alert
from ..adapters.storage.base import CatalogRepoPort, UptimeRepoPort
from ..core.alert_models import (
    RECENTLY_CLOSED_WINDOW_HOURS,
    AlertCandidate,
    AlertKind,
    AlertTiming,
)
from ..core.alert_policy import due_alert, effective_timing
from ..core.uptime_models import Incident, Monitor
from .errors import InvalidInput
from .monitors import get_monitor

logger = logging.getLogger("ssl_monitor.uptime.alerts")

ALERT_RETRY_BACKOFF_MINUTES = 5
MAX_ALERTS_PER_RUN = 50
# Stop a run early when the provider looks unreachable or the run takes too long, so
# a slow/unreachable SES never holds up the uptime checks for more than ~30 s.
MAX_CONSECUTIVE_FAILURES = 3
MAX_RUN_SECONDS = 30.0
# A closed incident's pending alerts are dropped once it leaves the candidate window.
EXPIRY_WARNING_HOURS = RECENTLY_CLOSED_WINDOW_HOURS - 1

AlertKey = tuple[int, AlertKind]  # (incident id, alert kind)


class AlertSettings(Protocol):
    """The settings dispatch needs (``config.settings.Settings`` satisfies it)."""

    alert_email: str | None
    alert_recipients: Sequence[str]
    uptime_reminder_minutes: int
    uptime_alert_delay_minutes: int
    public_url: str | None


@dataclass(frozen=True)
class AlertSummary:
    sent: int = 0
    failed: int = 0
    deferred: int = 0  # due, but over MAX_ALERTS_PER_RUN: sent on a later run


class NoAlertRecipients(InvalidInput):
    """Nobody to send to: no ALERT_EMAIL / ALERT_RECIPIENTS and no matching rule."""


class AlertNotSent(Exception):
    """The email provider refused the message."""


class _SendOutcome(Enum):
    SENT = "sent"
    NO_RECIPIENTS = "no_recipients"  # a configuration gap, not a provider failure
    REFUSED = "refused"  # the provider refused or could not be reached


class AlertBackoff:
    """What one worker process remembers between dispatch runs (keep one instance for
    the process lifetime): when each alert last failed to send, and which warnings
    were already logged. Reads no clock — callers pass ``now``."""

    def __init__(
        self, retry_after: timedelta = timedelta(minutes=ALERT_RETRY_BACKOFF_MINUTES)
    ) -> None:
        self._retry_after = retry_after
        self._failed_at: dict[AlertKey, datetime] = {}
        self._warned: set[Hashable] = set()

    def blocked(self, key: AlertKey, now: datetime) -> bool:
        """True while a recent failure of this alert is still backing off."""
        failed_at = self._failed_at.get(key)
        return failed_at is not None and now - failed_at < self._retry_after

    def failed(self, key: AlertKey, now: datetime) -> None:
        self._failed_at[key] = now

    def succeeded(self, key: AlertKey) -> None:
        self._failed_at.pop(key, None)

    def first_warning(self, key: Hashable) -> bool:
        """True the first time ``key`` is seen (log the warning then), False after."""
        if key in self._warned:
            return False
        self._warned.add(key)
        return True


# ── Recipients ───────────────────────────────────────────────────


def default_recipients(settings: AlertSettings) -> list[str]:
    """ALERT_EMAIL + ALERT_RECIPIENTS, lower-cased and de-duplicated."""
    return _unique([settings.alert_email or "", *settings.alert_recipients])


def recipients_for(
    catalog: CatalogRepoPort, monitor: Monitor, kind: AlertKind, settings: AlertSettings
) -> list[str]:
    return _unique([*default_recipients(settings), *catalog.recipients_for(monitor, kind)])


def _unique(emails: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for email in emails:
        cleaned = email.strip().lower()
        if cleaned:
            seen.setdefault(cleaned)
    return list(seen)


# ── Dispatch ─────────────────────────────────────────────────────


def dispatch_alerts(
    repo: UptimeRepoPort,
    catalog: CatalogRepoPort,
    notifier: NotifierPort,
    *,
    now: datetime,
    settings_like: AlertSettings,
    backoff: AlertBackoff | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AlertSummary:
    """Send the alerts due now, at most ``MAX_ALERTS_PER_RUN`` (the rest wait for a
    later run); the run also stops after ``MAX_CONSECUTIVE_FAILURES`` failed sends in a
    row or ``MAX_RUN_SECONDS``, so an unreachable provider can't stall uptime checks. Skipped while another worker holds the dispatch lock. One failing
    incident never blocks the rest.

    Delivery is at-least-once: an alert is marked sent only after the provider took
    it, so if marking it fails it is sent again on the next run."""
    with repo.alert_dispatch_lock() as acquired:
        if not acquired:
            logger.debug("Uptime alert dispatch skipped: another worker holds the lock")
            return AlertSummary()
        run = _Dispatch(repo, catalog, notifier, now, settings_like, backoff or AlertBackoff(),
                        clock)
        return run.all(repo.alert_candidates(now))


class _Dispatch:
    """One dispatch run: the stores, the notifier, the clock, the settings, the
    process-wide backoff, and the running totals."""

    def __init__(
        self,
        repo: UptimeRepoPort,
        catalog: CatalogRepoPort,
        notifier: NotifierPort,
        now: datetime,
        settings: AlertSettings,
        backoff: AlertBackoff,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo, self.catalog, self.notifier = repo, catalog, notifier
        self.now, self.settings, self.backoff = now, settings, backoff
        self.sent = self.failed = self.failures_in_a_row = 0
        self.in_flight: AlertKey | None = None
        self.clock = clock
        self.started = clock()

    @property
    def budget_spent(self) -> bool:
        return (
            self.sent + self.failed >= MAX_ALERTS_PER_RUN
            or self.failures_in_a_row >= MAX_CONSECUTIVE_FAILURES
            or self.clock() - self.started >= MAX_RUN_SECONDS
        )

    def all(self, candidates: Iterable[AlertCandidate]) -> AlertSummary:
        deferred = 0
        for candidate in candidates:
            if self.budget_spent:
                deferred += self.due_kind(candidate) is not None
            else:
                self.incident_safely(candidate)
        summary = AlertSummary(sent=self.sent, failed=self.failed, deferred=deferred)
        _log_summary(summary)
        return summary

    def incident_safely(self, candidate: AlertCandidate) -> None:
        """An unexpected error (e.g. the database failing after the provider took the
        email) backs that alert off like a refused send, so it is never re-sent every
        tick, and counts towards stopping the run."""
        self.in_flight = None
        try:
            self.incident_alerts(candidate)
        except Exception:
            logger.exception("Uptime alert failed for incident %s", candidate.incident.id)
            if self.in_flight is not None:
                self.backoff.failed(self.in_flight, self.now)
            self.failed += 1
            self.failures_in_a_row += 1

    def incident_alerts(self, candidate: AlertCandidate) -> None:
        """Send this incident's due alerts in order, stopping at the first that fails.

        Only a provider failure counts towards MAX_CONSECUTIVE_FAILURES: an incident
        with nobody to email backs off without stopping alerts for other incidents."""
        self.warn_if_expiring(candidate)
        while not self.budget_spent and (kind := self.due_kind(candidate)) is not None:
            key = self.in_flight = (candidate.incident.id, kind)
            outcome = self.send(kind, candidate)
            if outcome is not _SendOutcome.SENT:
                self.backoff.failed(key, self.now)
                self.failed += 1
                self.failures_in_a_row += outcome is _SendOutcome.REFUSED
                return
            self.repo.mark_alert_sent(candidate.incident.id, kind, self.now)
            self.failures_in_a_row = 0
            self.backoff.succeeded(key)
            self.in_flight = None
            candidate = replace(candidate, incident=_marked(candidate.incident, kind, self.now))
            self.sent += 1

    def due_kind(self, candidate: AlertCandidate) -> AlertKind | None:
        """The alert due now, unless that alert is backing off after a failure."""
        kind = due_alert(candidate, self.timing(candidate.monitor), self.now)
        if kind is None or self.backoff.blocked((candidate.incident.id, kind), self.now):
            return None
        return kind

    def timing(self, monitor: Monitor) -> AlertTiming:
        return effective_timing(
            monitor,
            default_reminder_minutes=self.settings.uptime_reminder_minutes,
            default_alert_delay_minutes=self.settings.uptime_alert_delay_minutes,
        )

    def warn_if_expiring(self, candidate: AlertCandidate) -> None:
        """Warn once when a closed incident's owed alerts are about to be dropped."""
        incident = candidate.incident
        if incident.is_open:
            return
        if self.now - incident.resolved_at < timedelta(hours=EXPIRY_WARNING_HOURS):
            return
        if due_alert(candidate, self.timing(candidate.monitor), self.now) is None:
            return
        if self.backoff.first_warning(("expiring", incident.id)):
            logger.warning(
                "Incident %s on monitor %s (%s) closed at %s with alerts still unsent; "
                "they are dropped %d h after it closed",
                incident.id, candidate.monitor.id, candidate.monitor.name,
                incident.resolved_at.isoformat(), RECENTLY_CLOSED_WINDOW_HOURS,
            )

    def send(self, kind: AlertKind, candidate: AlertCandidate) -> _SendOutcome:
        monitor, key = candidate.monitor, (candidate.incident.id, kind)
        to = recipients_for(self.catalog, monitor, kind, self.settings)
        if not to:
            if self.backoff.first_warning(("no-recipients", key)):
                logger.warning(
                    "No recipients for %s alert on monitor %s (%s): set ALERT_EMAIL / "
                    "ALERT_RECIPIENTS or add an alert rule", kind.value.upper(), monitor.id,
                    monitor.name,
                )
            return _SendOutcome.NO_RECIPIENTS
        message = format_uptime_alert(
            kind, candidate, now=self.now, to=to, public_url=self.settings.public_url
        )
        if not self.notifier.send_email(message):
            return _SendOutcome.REFUSED
        logger.info(
            "Uptime alert sent: %s for monitor %s (%s), incident %s -> %s",
            kind.value.upper(), monitor.id, monitor.name, candidate.incident.id, ", ".join(to),
        )
        return _SendOutcome.SENT


def _log_summary(summary: AlertSummary) -> None:
    if summary.sent or summary.failed:
        logger.info("Uptime alerts: %d sent, %d failed", summary.sent, summary.failed)
    if summary.deferred:
        logger.warning("Uptime alerts: %d deferred to the next run (at most %d per run)",
                       summary.deferred, MAX_ALERTS_PER_RUN)


def _marked(incident: Incident, kind: AlertKind, at: datetime) -> Incident:
    """The incident as ``mark_alert_sent`` leaves it (so the policy can be re-asked)."""
    match kind:
        case AlertKind.DOWN:
            return replace(incident, down_alert_sent_at=at)
        case AlertKind.REMINDER:
            return replace(incident, last_reminder_sent_at=at,
                           reminder_count=incident.reminder_count + 1)
        case AlertKind.RECOVERED:
            return replace(incident, recovery_alert_sent_at=at)
    return incident


# ── Test alert ───────────────────────────────────────────────────


def send_test_alert(
    repo: UptimeRepoPort,
    catalog: CatalogRepoPort,
    notifier: NotifierPort,
    monitor_id: int,
    *,
    now: datetime,
    settings_like: AlertSettings,
) -> list[str]:
    """Send a [TEST] email for one monitor to everyone who would get its alerts.

    Returns the recipients. Raises MonitorNotFound, NoAlertRecipients, or AlertNotSent
    (provider refused). Nothing is recorded."""
    monitor = get_monitor(repo, monitor_id)
    to = recipients_for(catalog, monitor, AlertKind.TEST, settings_like)
    if not to:
        raise NoAlertRecipients(
            "No alert recipients: set ALERT_EMAIL / ALERT_RECIPIENTS or add an alert rule"
        )
    candidate = _test_candidate(repo, catalog, monitor, now)
    message = format_uptime_alert(
        AlertKind.TEST, candidate, now=now, to=to, public_url=settings_like.public_url
    )
    if not notifier.send_email(message):
        raise AlertNotSent("The email provider refused the test alert; check the server logs")
    logger.info("Uptime test alert sent for monitor %s -> %s", monitor.id, ", ".join(to))
    return to


def _test_candidate(
    repo: UptimeRepoPort, catalog: CatalogRepoPort, monitor: Monitor, now: datetime
) -> AlertCandidate:
    """The monitor's open incident if it has one, else a placeholder starting now."""
    latest = repo.list_incidents(monitor.id, limit=1)
    incident = latest[0] if latest and latest[0].is_open else Incident(
        id=0, monitor_id=monitor.id, started_at=now
    )
    customer_name, group_name = catalog_names(catalog, monitor)
    return AlertCandidate(incident, monitor, customer_name=customer_name, group_name=group_name)


def catalog_names(catalog: CatalogRepoPort, monitor: Monitor) -> tuple[str | None, str | None]:
    """The monitor's (customer name, group name), for the email subject."""
    customer = catalog.get_customer(monitor.customer_id) if monitor.customer_id else None
    group = catalog.get_group(monitor.group_id) if monitor.group_id else None
    return (customer.name if customer else None, group.name if group else None)
