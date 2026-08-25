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
from datetime import datetime, timedelta

from ..adapters.notifier.base import NotifierPort
from ..adapters.storage.base import StoragePort
from ..core.cert_probe import probe_cert
from ..core.models import Alert, AlertDecision, DomainRecord, ProbeResult, Status
from ..core.status import classify_status, days_remaining
from ..core.thresholds import decide_alert

# (host, port, timeout) -> ProbeResult
ProbeFn = Callable[[str, int, float], ProbeResult]

# How long an agent-managed record may go without a fresh report before we treat
# the agent as down. The agent's default interval is 5 minutes, so this is ~72
# missed reports — long enough to ride out a restart, short enough to notice.
DEFAULT_AGENT_STALE_AFTER_HOURS = 6.0


@dataclass(frozen=True)
class CheckOutcome:
    """Per-domain result of a check: the updated record and whether it alerted."""

    record: DomainRecord
    alerted: bool
    threshold: int | None
    probed: bool = True          # False for agent-managed rows (never probed here)
    stale_alerted: bool = False  # an "agent stopped reporting" alert was sent


def refresh_status(
    record: DomainRecord,
    *,
    now: datetime,
    thresholds: Sequence[int],
    timeout: float,
    probe_fn: ProbeFn = probe_cert,
) -> DomainRecord:
    """Probe one domain and update status / days / error / last_checked."""
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


def evaluate_agent_record(
    record: DomainRecord,
    *,
    now: datetime,
    thresholds: Sequence[int],
    stale_after: timedelta,
) -> tuple[DomainRecord, AlertDecision, Alert | None]:
    """Evaluate an agent-managed record **without probing it**.

    The host sits inside a private network, so the central server cannot reach
    it — probing would fail and overwrite the agent's good data with a false
    ``unreachable``, which then suppresses every expiry alert (``decide_alert``
    never fires on ``days_remaining is None``). That is the bug this exists to
    prevent.

    Instead we re-derive ``days_remaining`` from the stored ``not_after`` so the
    countdown keeps running between reports, and apply the ordinary threshold
    rules to it. A cert therefore still pages as it approaches expiry even if
    the agent goes quiet — and the agent going quiet is itself alerted, once.
    """
    last_report = record.last_checked_at
    is_stale = last_report is None or (now - last_report) > stale_after

    if record.not_after is not None:
        record.days_remaining = days_remaining(record.not_after, now)
        # We have real cert data from the agent; the host is reachable *to the
        # agent*, which is what matters for classification.
        record.status = classify_status(True, record.days_remaining, thresholds)
    else:
        # The agent has never delivered an expiry (never reported, or its own
        # probe failed). Genuinely unknown.
        record.days_remaining = None
        record.status = Status.UNREACHABLE

    if record.alerts_enabled:
        decision = decide_alert(record.days_remaining, thresholds, record.last_alert_threshold)
        record.last_alert_threshold = decision.new_last_alert
    else:
        decision = AlertDecision(
            should_alert=False, threshold=None, new_last_alert=record.last_alert_threshold
        )

    stale_alert: Alert | None = None
    if is_stale:
        silent_for = "never reported" if last_report is None else _humanize(now - last_report)
        record.last_error = (
            f"agent {record.agent_id or '(unknown)'} has not reported: {silent_for}"
        )
        # Page once per outage, not once per checker pass.
        if record.alerts_enabled and not record.stale_alert_sent:
            stale_alert = Alert(
                domain=record.domain,
                status=Status.UNREACHABLE,
                days_remaining=record.days_remaining,
                threshold=None,
                not_after=record.not_after,
                detail=(
                    f"Agent '{record.agent_id or 'unknown'}' has not reported for this "
                    f"host ({silent_for}). Certificate data shown may be out of date — "
                    "check that the agent is running and can reach the CertWatch API."
                ),
            )
            record.stale_alert_sent = True
    else:
        record.stale_alert_sent = False  # agent is healthy again

    return record, decision, stale_alert


def _humanize(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def run_check(
    storage: StoragePort,
    notifier: NotifierPort,
    *,
    thresholds: Sequence[int],
    timeout: float,
    now: datetime,
    probe_fn: ProbeFn = probe_cert,
    agent_stale_after_hours: float = DEFAULT_AGENT_STALE_AFTER_HOURS,
) -> dict:
    """Check every stored domain, persist results, fire de-duplicated alerts.

    Direct domains are probed over TLS. Agent-managed domains are evaluated from
    the data their agent last pushed — never probed from here.

    Returns a JSON-serializable summary suitable for a Lambda response.
    """
    outcomes: list[CheckOutcome] = []
    stale_after = timedelta(hours=agent_stale_after_hours)

    for record in storage.list():
        stale_alert: Alert | None = None

        if record.is_agent_managed:
            updated, decision, stale_alert = evaluate_agent_record(
                record, now=now, thresholds=thresholds, stale_after=stale_after
            )
        else:
            updated, decision = check_one(
                record,
                now=now,
                thresholds=thresholds,
                timeout=timeout,
                probe_fn=probe_fn,
            )
        storage.put(updated)

        if stale_alert is not None:
            notifier.notify(stale_alert)

        if decision.should_alert:
            notifier.notify(
                Alert(
                    domain=updated.domain,
                    status=updated.status,
                    days_remaining=updated.days_remaining,
                    threshold=decision.threshold,
                    not_after=updated.not_after,
                )
            )
        outcomes.append(
            CheckOutcome(
                updated,
                decision.should_alert,
                decision.threshold,
                probed=not record.is_agent_managed,
                stale_alerted=stale_alert is not None,
            )
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
        "probed": sum(1 for o in outcomes if o.probed),
        "alerts_sent": sum(1 for o in outcomes if o.alerted) + sum(1 for o in outcomes if o.stale_alerted),
        "stale_agents": sum(1 for o in outcomes if o.stale_alerted),
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
                "source": o.record.source,
                "agent_id": o.record.agent_id,
            }
            for o in outcomes
        ],
    }
