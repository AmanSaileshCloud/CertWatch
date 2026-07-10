"""Checker orchestration.

Ties the pure core (probe → days_remaining → status → alert decision) to the
storage and notifier ports. The probe function and clock are injected, so the
whole flow is unit-testable with the in-memory store, the console notifier, and
a fake probe — no network, no AWS.

This module is plain and adapter-agnostic: it depends only on the ports
(``StoragePort`` / ``NotifierPort``) and core, never on boto3.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..adapters.notifier.base import NotifierPort
from ..adapters.storage.base import StoragePort
from ..core.cert_probe import probe_cert
from ..core.models import Alert, AlertDecision, DomainRecord, ProbeResult
from ..core.status import classify_status, days_remaining
from ..core.thresholds import decide_alert

# (host, port, timeout) -> ProbeResult
ProbeFn = Callable[[str, int, float], ProbeResult]


@dataclass(frozen=True)
class CheckOutcome:
    """Per-domain result of a check: the updated record and whether it alerted."""

    record: DomainRecord
    alerted: bool
    threshold: int | None


def refresh_status(
    record: DomainRecord,
    *,
    now: datetime,
    thresholds: Sequence[int],
    timeout: float,
    probe_fn: ProbeFn = probe_cert,
) -> DomainRecord:
    """Probe one domain and update status / days / error / last_checked.

    Does NOT touch the alert-dedup state (``last_alert_threshold``), so it's safe
    to call on add — an already-expiring new domain still gets its first alert on
    the next real checker pass. On an unreachable host, expiry fields are cleared
    and ``last_error`` is set — never a false alert.
    """
    result = probe_fn(record.host, record.port, timeout)

    if result.reachable and result.not_after is not None:
        record.days_remaining = days_remaining(result.not_after, now)
        record.not_after = result.not_after
        record.last_error = None
    else:
        record.not_after = None
        record.days_remaining = None
        record.last_error = result.error

    record.status = classify_status(result.reachable, record.days_remaining, thresholds)
    record.last_checked_at = now
    return record


def check_one(
    record: DomainRecord,
    *,
    now: datetime,
    thresholds: Sequence[int],
    timeout: float,
    probe_fn: ProbeFn = probe_cert,
) -> tuple[DomainRecord, AlertDecision]:
    """Probe one domain and return the updated record + alert decision.

    Mutates and returns ``record`` (caller persists it).
    """
    refresh_status(record, now=now, thresholds=thresholds, timeout=timeout, probe_fn=probe_fn)
    days = record.days_remaining

    if record.alerts_enabled:
        decision = decide_alert(days, thresholds, record.last_alert_threshold)
        record.last_alert_threshold = decision.new_last_alert
    else:
        # Alerts muted for this domain — don't notify and don't advance dedup
        # state, so re-enabling later alerts fresh.
        decision = AlertDecision(
            should_alert=False, threshold=None, new_last_alert=record.last_alert_threshold
        )
    return record, decision


def run_check(
    storage: StoragePort,
    notifier: NotifierPort,
    *,
    thresholds: Sequence[int],
    timeout: float,
    now: datetime,
    probe_fn: ProbeFn = probe_cert,
) -> dict:
    """Check every stored domain, persist results, fire de-duplicated alerts.

    Returns a JSON-serializable summary suitable for a Lambda response.
    """
    outcomes: list[CheckOutcome] = []

    for record in storage.list():
        updated, decision = check_one(
            record,
            now=now,
            thresholds=thresholds,
            timeout=timeout,
            probe_fn=probe_fn,
        )
        storage.put(updated)

        if decision.should_alert:
            notifier.notify(
                Alert(
                    domain=updated.domain,
                    status=updated.status,
                    days_remaining=updated.days_remaining,
                    threshold=decision.threshold,
                    not_after=updated.not_after,
                    recipients=list(updated.notify_emails),
                )
            )
        outcomes.append(
            CheckOutcome(updated, decision.should_alert, decision.threshold)
        )

    return summarize(outcomes, now)


def summarize(outcomes: Sequence[CheckOutcome], now: datetime) -> dict:
    """Aggregate per-domain outcomes into a run summary."""
    by_status: dict[str, int] = {}
    for o in outcomes:
        by_status[o.record.status.value] = by_status.get(o.record.status.value, 0) + 1

    return {
        "checked_at": now.isoformat(),
        "checked": len(outcomes),
        "alerts_sent": sum(1 for o in outcomes if o.alerted),
        "by_status": by_status,
        "domains": [
            {
                "domain": o.record.domain,
                "status": o.record.status.value,
                "days_remaining": o.record.days_remaining,
                "not_after": o.record.not_after.isoformat() if o.record.not_after else None,
                "alerted": o.alerted,
                "threshold": o.threshold,
                "error": o.record.last_error,
            }
            for o in outcomes
        ],
    }
