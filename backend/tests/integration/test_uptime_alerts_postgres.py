"""Uptime alerts end to end against PostgreSQL: check cycles + dispatch with real repos."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.catalog_postgres import PostgresCatalogRepo
from backend.ssl_monitor.adapters.storage.uptime_postgres import PostgresUptimeRepo
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.alert_models import AlertRule
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor
from backend.ssl_monitor.services.uptime_alerts import AlertSummary, dispatch_alerts
from backend.ssl_monitor.services.uptime_checker import run_cycle

pytestmark = pytest.mark.postgres

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)


def test_down_then_recovered_emails_through_a_full_cycle(pg):
    repo, catalog, notifier = PostgresUptimeRepo(pg), PostgresCatalogRepo(pg), ConsoleNotifier()
    customer = catalog.add_customer("Acme")
    group = catalog.add_group(customer.id, "Shops")
    catalog.add_alert_rule(AlertRule(id=0, target="Cust@Acme.test", customer_id=customer.id))
    monitor = repo.add_monitor(Monitor(
        id=0, name="Shop", url="https://shop.example.com", interval_seconds=60,
        next_check_at=NOW, customer_id=customer.id, group_id=group.id,
    ))
    settings = Settings.from_env({"ALERT_EMAIL": "ops@acme.test"})
    outcomes = iter([False, False, False, True])

    async def probe(m, checked_at):
        is_up = next(outcomes)
        return CheckResult(m.id, checked_at, is_up=is_up, error=None if is_up else "HTTP 503")

    alert_summaries = []
    for cycle in range(4):
        now = NOW + dt.timedelta(seconds=60 * cycle)
        asyncio.run(run_cycle(repo, probe, now=now, max_concurrency=5, clock=lambda now=now: now))
        alert_summaries.append(
            dispatch_alerts(repo, catalog, notifier, now=now, settings_like=settings))

    assert [s.sent for s in alert_summaries] == [0, 0, 1, 1]
    assert all(s.failed == 0 for s in alert_summaries)
    down, recovered = notifier.sent_emails
    assert down.subject == "[DOWN] Acme · Shops · Shop"
    assert down.to == ("ops@acme.test", "cust@acme.test")
    assert recovered.subject.startswith("[RECOVERED after 3m]")

    [incident] = repo.list_incidents(monitor.id)
    assert incident.down_alert_sent_at == NOW + dt.timedelta(seconds=120)
    assert incident.recovery_alert_sent_at == NOW + dt.timedelta(seconds=180)

    later = NOW + dt.timedelta(hours=1)
    assert dispatch_alerts(repo, catalog, notifier, now=later, settings_like=settings) == (
        AlertSummary())


def test_only_one_worker_holds_the_dispatch_lock(pg):
    first, second = PostgresUptimeRepo(pg), PostgresUptimeRepo(pg)
    with first.alert_dispatch_lock() as got_first:
        with second.alert_dispatch_lock() as got_second:
            assert (got_first, got_second) == (True, False)
    with second.alert_dispatch_lock() as got_after_release:
        assert got_after_release is True


def test_dispatch_is_skipped_while_another_worker_holds_the_lock(pg):
    repo, catalog, notifier = PostgresUptimeRepo(pg), PostgresCatalogRepo(pg), ConsoleNotifier()
    monitor = repo.add_monitor(Monitor(id=0, name="Shop", url="https://shop.example.com",
                                       interval_seconds=60, next_check_at=NOW))
    repo.record_checks([CheckResult(monitor.id, NOW, is_up=False)] * 3, evaluate)
    settings = Settings.from_env({"ALERT_EMAIL": "ops@acme.test"})

    with PostgresUptimeRepo(pg).alert_dispatch_lock():
        skipped = dispatch_alerts(repo, catalog, notifier, now=NOW, settings_like=settings)
    sent = dispatch_alerts(repo, catalog, notifier, now=NOW, settings_like=settings)

    assert (skipped, sent) == (AlertSummary(), AlertSummary(sent=1))


def test_outage_that_ended_while_alerts_were_off_stays_silent(pg):
    """Re-enabling alerts saves ``alerts_enabled_at`` and the candidate query returns it."""
    from backend.ssl_monitor.services.monitors import update_monitor

    repo, catalog, notifier = PostgresUptimeRepo(pg), PostgresCatalogRepo(pg), ConsoleNotifier()
    monitor = repo.add_monitor(Monitor(id=0, name="Shop", url="https://shop.example.com",
                                       interval_seconds=60, next_check_at=NOW,
                                       alerts_enabled=False))
    checks = [CheckResult(monitor.id, NOW + dt.timedelta(minutes=m), is_up=m == 3,
                          error=None if m == 3 else "HTTP 503") for m in range(4)]
    repo.record_checks(checks, evaluate)  # down for 3 checks, then recovered
    enabled_at = NOW + dt.timedelta(hours=1)
    update_monitor(repo, catalog, monitor.id, {"alerts_enabled": True},
                   check_url=lambda url: url, now=enabled_at)

    assert repo.get_monitor(monitor.id).alerts_enabled_at == enabled_at
    settings = Settings.from_env({"ALERT_EMAIL": "ops@acme.test"})
    assert dispatch_alerts(repo, catalog, notifier, now=enabled_at,
                           settings_like=settings) == AlertSummary()
    assert notifier.sent_emails == []
