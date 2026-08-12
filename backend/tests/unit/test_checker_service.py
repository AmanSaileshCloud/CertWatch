"""Tests for the checker orchestration with the in-memory store, the console
notifier, and a fake probe. No network, no AWS."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.core.models import DomainRecord, ProbeResult, Status
from backend.ssl_monitor.services.checker import check_one, run_check

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)
THRESHOLDS = [30, 14, 7, 1]


def fake_probe(results: dict[str, ProbeResult]):
    """Build a probe_fn that returns a canned ProbeResult keyed by host."""

    def _probe(host: str, port: int, timeout: float) -> ProbeResult:
        return results[host]

    return _probe


def reachable(host: str, days: int) -> ProbeResult:
    return ProbeResult(host, 443, reachable=True, not_after=NOW + dt.timedelta(days=days))


def unreachable(host: str, error: str = "connection refused") -> ProbeResult:
    return ProbeResult(host, 443, reachable=False, error=error)


def seed(store: MemoryStorage, host: str, **kw) -> DomainRecord:
    rec = DomainRecord.new(host=host, port=443, created_at=NOW)
    for k, v in kw.items():
        setattr(rec, k, v)
    store.put(rec)
    return rec


# ---- check_one -------------------------------------------------------------

def test_check_one_healthy_sets_ok_no_alert():
    rec = DomainRecord.new(host="ok.test", port=443, created_at=NOW)
    updated, decision = check_one(
        rec, now=NOW, thresholds=THRESHOLDS, timeout=5,
        probe_fn=fake_probe({"ok.test": reachable("ok.test", 90)}),
    )
    assert updated.status is Status.OK
    assert updated.days_remaining == 90
    assert updated.last_error is None
    assert decision.should_alert is False


def test_check_one_unreachable_clears_expiry_no_alert():
    rec = DomainRecord.new(host="down.test", port=443, created_at=NOW)
    updated, decision = check_one(
        rec, now=NOW, thresholds=THRESHOLDS, timeout=5,
        probe_fn=fake_probe({"down.test": unreachable("down.test", "timeout")}),
    )
    assert updated.status is Status.UNREACHABLE
    assert updated.not_after is None
    assert updated.days_remaining is None
    assert updated.last_error == "timeout"
    assert decision.should_alert is False


# ---- run_check (full orchestration) ----------------------------------------

def test_run_check_fires_one_alert_for_expiring():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    seed(store, "soon.test")
    summary = run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
        probe_fn=fake_probe({"soon.test": reachable("soon.test", 6)}),
    )
    assert summary["checked"] == 1
    assert summary["alerts_sent"] == 1
    assert len(notifier.sent) == 1
    assert notifier.sent[0].threshold == 7
    stored = store.get("soon.test:443")
    assert stored.status is Status.WARNING
    assert stored.last_alert_threshold == 7


def test_run_check_is_idempotent_across_runs():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    seed(store, "soon.test")
    probe = fake_probe({"soon.test": reachable("soon.test", 6)})

    run_check(store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW, probe_fn=probe)
    run_check(store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW, probe_fn=probe)

    assert len(notifier.sent) == 1  # second run must not re-alert


def test_run_check_escalates_to_more_urgent_threshold():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    seed(store, "soon.test")

    run_check(store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
              probe_fn=fake_probe({"soon.test": reachable("soon.test", 10)}))  # 14
    run_check(store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
              probe_fn=fake_probe({"soon.test": reachable("soon.test", 3)}))   # 7

    assert [a.threshold for a in notifier.sent] == [14, 7]


def test_run_check_unreachable_does_not_alert_or_touch_dedup():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    # Previously alerted at 7; now the host goes down.
    seed(store, "flaky.test", last_alert_threshold=7)
    summary = run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
        probe_fn=fake_probe({"flaky.test": unreachable("flaky.test")}),
    )
    assert summary["alerts_sent"] == 0
    assert notifier.sent == []
    assert store.get("flaky.test:443").last_alert_threshold == 7  # preserved


def test_run_check_renewal_resets_dedup():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    seed(store, "renewed.test", last_alert_threshold=7)
    run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
        probe_fn=fake_probe({"renewed.test": reachable("renewed.test", 90)}),
    )
    stored = store.get("renewed.test:443")
    assert stored.status is Status.OK
    assert stored.last_alert_threshold is None


def test_run_check_expired_alerts_most_urgent():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    seed(store, "expired.test")
    run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
        probe_fn=fake_probe({"expired.test": reachable("expired.test", -5)}),
    )
    assert store.get("expired.test:443").status is Status.EXPIRED
    assert notifier.sent[0].threshold == 1


def test_run_check_summary_aggregates_mixed_fleet():
    store = MemoryStorage()
    notifier = ConsoleNotifier()
    seed(store, "ok.test")
    seed(store, "warn.test")
    seed(store, "down.test")
    summary = run_check(
        store, notifier, thresholds=THRESHOLDS, timeout=5, now=NOW,
        probe_fn=fake_probe({
            "ok.test": reachable("ok.test", 90),
            "warn.test": reachable("warn.test", 5),
            "down.test": unreachable("down.test"),
        }),
    )
    assert summary["checked"] == 3
    assert summary["alerts_sent"] == 1
    assert summary["by_status"] == {"ok": 1, "warning": 1, "unreachable": 1}
