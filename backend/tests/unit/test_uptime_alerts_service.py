"""Uptime alert dispatch — in-memory repos, ConsoleNotifier, fixed clock."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest

from backend.ssl_monitor.adapters.notifier.console import ConsoleNotifier
from backend.ssl_monitor.adapters.storage.catalog_memory import MemoryCatalogRepo
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.alert_models import AlertKind, AlertRule
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor
from backend.ssl_monitor.services.errors import MonitorNotFound
from backend.ssl_monitor.services.monitors import pause_monitor, update_monitor
from backend.ssl_monitor.services import uptime_alerts
from backend.ssl_monitor.services.uptime_alerts import (
    AlertBackoff,
    AlertNotSent,
    AlertSummary,
    NoAlertRecipients,
    dispatch_alerts,
    send_test_alert,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
OPS = "ops@acme.test"


def _settings(**env: str) -> Settings:
    return Settings.from_env({"ALERT_EMAIL": OPS} | env)


class FailingNotifier(ConsoleNotifier):
    """Like SES refusing the message."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def send_email(self, message) -> bool:
        self.attempts += 1
        return False


class World:
    def __init__(self, settings: Settings | None = None, **monitor_fields) -> None:
        self.catalog = MemoryCatalogRepo()
        self.repo = MemoryUptimeRepo()
        self.notifier = ConsoleNotifier()
        self.settings = settings or _settings()
        self.monitor = self.repo.add_monitor(Monitor(
            id=0, name="Shop", url="https://shop.test", interval_seconds=60, next_check_at=T0,
            **monitor_fields,
        ))

    def check(self, minute: float, *, up: bool) -> None:
        result = CheckResult(
            monitor_id=self.monitor.id, checked_at=T0 + timedelta(minutes=minute), is_up=up,
            status_code=200 if up else 503, error=None if up else "HTTP 503",
        )
        self.repo.record_checks([result], evaluate)

    def go_down(self, minute: float = 0) -> None:
        """Three failures a minute apart; the incident starts at ``minute``."""
        for offset in range(3):
            self.check(minute + offset, up=False)

    def dispatch(self, minute: float, notifier=None, backoff=None) -> AlertSummary:
        return dispatch_alerts(
            self.repo, self.catalog, notifier or self.notifier,
            now=T0 + timedelta(minutes=minute), settings_like=self.settings, backoff=backoff,
        )

    @property
    def subjects(self) -> list[str]:
        return [m.subject for m in self.notifier.sent_emails]

    @property
    def incident(self):
        return self.repo.list_incidents(self.monitor.id)[0]


# ── DOWN / REMINDER / RECOVERED ──────────────────────────────────


def test_first_down_alert_is_sent_once_and_marked():
    w = World()
    w.go_down()

    assert w.dispatch(2) == AlertSummary(sent=1, failed=0)
    assert w.subjects == ["[DOWN] Shop"]
    assert w.notifier.sent_emails[0].to == (OPS,)
    assert w.incident.down_alert_sent_at == T0 + timedelta(minutes=2)

    assert w.dispatch(3) == AlertSummary()  # next cycle: nothing new
    assert len(w.subjects) == 1


def test_reminders_repeat_every_interval():
    w = World()
    w.go_down()
    w.dispatch(2)  # DOWN

    w.dispatch(31.9)
    assert len(w.subjects) == 1
    w.dispatch(32)  # 30 min after DOWN
    w.dispatch(50)
    w.dispatch(62)  # 30 min after the first reminder
    # the duration in the subject is the outage length so far (it started at T0)
    assert [s.split("]")[0] for s in w.subjects] == ["[DOWN", "[STILL DOWN 32m",
                                                       "[STILL DOWN 1h 2m"]
    assert w.incident.reminder_count == 2
    assert w.incident.last_reminder_sent_at == T0 + timedelta(minutes=62)


def test_per_monitor_reminder_off():
    w = World(reminder_minutes=0)
    w.go_down()
    w.dispatch(2)
    w.dispatch(600)
    assert len(w.subjects) == 1


def test_recovered_is_sent_once():
    w = World()
    w.go_down()
    w.dispatch(2)
    w.check(10, up=True)

    assert w.dispatch(10) == AlertSummary(sent=1)
    assert w.subjects[-1].startswith("[RECOVERED after 10m]")
    assert w.incident.recovery_alert_sent_at == T0 + timedelta(minutes=10)
    w.dispatch(11)
    assert len(w.subjects) == 2


@pytest.mark.parametrize("close", ["pause", "edit"])
def test_paused_or_edited_closure_sends_no_recovered(close):
    w = World()
    w.go_down()
    w.dispatch(2)
    now = T0 + timedelta(minutes=5)
    if close == "pause":
        pause_monitor(w.repo, w.monitor.id, now)
    else:
        update_monitor(w.repo, w.catalog, w.monitor.id, {"url": "https://new.test"},
                       check_url=lambda url: url, now=now)
    w.dispatch(6)
    assert w.subjects == ["[DOWN] Shop"]


def test_alert_setting_change_does_not_close_the_incident():
    w = World()
    w.go_down()
    update_monitor(w.repo, w.catalog, w.monitor.id,
                   {"reminder_minutes": 15, "alert_delay_minutes": 5, "alerts_enabled": True},
                   check_url=lambda url: url, now=T0 + timedelta(minutes=2))
    assert w.incident.is_open
    assert w.repo.get_monitor(w.monitor.id).state.value == "down"


# ── Delay ────────────────────────────────────────────────────────


def test_blip_shorter_than_the_delay_is_silent():
    w = World(alert_delay_minutes=5)
    w.go_down()
    w.dispatch(3)
    w.check(4, up=True)  # 4-minute outage
    assert w.dispatch(4) == AlertSummary()
    assert w.subjects == []


def test_down_waits_for_the_delay():
    w = World(alert_delay_minutes=5)
    w.go_down()
    w.dispatch(4.9)
    assert w.subjects == []
    w.dispatch(5)
    assert w.subjects == ["[DOWN] Shop"]


def test_global_delay_applies_when_monitor_has_none():
    w = World(_settings(UPTIME_ALERT_DELAY_MINUTES="10"))
    w.go_down()
    w.dispatch(9)
    assert w.subjects == []
    w.dispatch(10)
    assert len(w.subjects) == 1


def test_quick_flap_with_no_delay_sends_down_then_recovered_in_one_cycle():
    w = World()
    w.go_down()
    w.check(3, up=True)  # recovered before any dispatch

    assert w.dispatch(3) == AlertSummary(sent=2)
    assert [s.split(" ")[0] for s in w.subjects] == ["[DOWN]", "[RECOVERED"]
    assert w.incident.down_alert_sent_at and w.incident.recovery_alert_sent_at


def test_long_outage_closed_before_dispatch_with_delay_sends_both():
    w = World(alert_delay_minutes=5)
    w.go_down()
    w.check(8, up=True)
    assert w.dispatch(8) == AlertSummary(sent=2)


# ── Disabled, failures, recipients ───────────────────────────────


def test_alerts_disabled_sends_nothing():
    w = World(alerts_enabled=False)
    w.go_down()
    assert w.dispatch(60) == AlertSummary()
    assert w.subjects == []
    assert w.incident.down_alert_sent_at is None


def test_send_failure_is_not_marked_and_retried_next_cycle():
    w = World()
    w.go_down()
    failing = FailingNotifier()

    assert w.dispatch(2, failing) == AlertSummary(sent=0, failed=1)
    assert w.incident.down_alert_sent_at is None

    assert w.dispatch(3) == AlertSummary(sent=1)
    assert w.incident.down_alert_sent_at == T0 + timedelta(minutes=3)


def test_closed_incident_failure_stops_before_recovered():
    w = World()
    w.go_down()
    w.check(3, up=True)
    failing = FailingNotifier()
    assert w.dispatch(3, failing) == AlertSummary(failed=1)
    assert failing.attempts == 1  # RECOVERED is not tried after DOWN failed


def test_no_recipients_is_not_marked(caplog):
    w = World(Settings.from_env({}))
    w.go_down()
    with caplog.at_level(logging.WARNING):
        assert w.dispatch(2) == AlertSummary(failed=1)
    assert w.subjects == []
    assert w.incident.down_alert_sent_at is None
    assert "No recipients" in caplog.text


def test_recipients_are_defaults_plus_rules_deduplicated():
    w = World(_settings(ALERT_RECIPIENTS="Team@Acme.test, ops@acme.test"))
    customer = w.catalog.add_customer("Acme")
    w.monitor = w.repo.change_monitor(w.monitor.id, {"customer_id": customer.id}, now=T0)
    w.catalog.add_alert_rule(AlertRule(id=0, target="Boss@Acme.test", customer_id=customer.id))
    w.catalog.add_alert_rule(AlertRule(id=0, target="ops@acme.test"))  # global, duplicate
    w.catalog.add_alert_rule(AlertRule(id=0, target="mon@acme.test", monitor_id=w.monitor.id,
                                       on_down=False))
    w.go_down()
    w.dispatch(2)
    assert w.notifier.sent_emails[0].to == (OPS, "team@acme.test", "boss@acme.test")

    w.check(5, up=True)
    w.dispatch(5)
    assert set(w.notifier.sent_emails[1].to) == {
        OPS, "team@acme.test", "boss@acme.test", "mon@acme.test"}


def test_rule_recipients_alone_are_enough():
    w = World(Settings.from_env({}))
    w.catalog.add_alert_rule(AlertRule(id=0, target="only@acme.test"))
    w.go_down()
    assert w.dispatch(2) == AlertSummary(sent=1)
    assert w.notifier.sent_emails[0].to == ("only@acme.test",)


def test_one_broken_candidate_does_not_block_the_others(monkeypatch):
    w = World()
    other = w.repo.add_monitor(Monitor(id=0, name="Blog", url="https://blog.test",
                                       interval_seconds=60, next_check_at=T0))
    w.go_down()
    for minute in range(3):
        w.repo.record_checks([CheckResult(other.id, T0 + timedelta(minutes=minute), False)],
                             evaluate)
    real_mark = w.repo.mark_alert_sent

    def flaky_mark(incident_id, kind, at):
        if incident_id == w.incident.id:
            raise RuntimeError("db hiccup")
        real_mark(incident_id, kind, at)

    monkeypatch.setattr(w.repo, "mark_alert_sent", flaky_mark)
    assert w.dispatch(2) == AlertSummary(sent=1, failed=1)


def test_mark_failing_after_a_send_resends_next_cycle(monkeypatch):
    """At-least-once: the email went out but was not recorded, so it goes out again."""
    w = World()
    w.go_down()
    real_mark = w.repo.mark_alert_sent

    def broken_mark(incident_id, kind, at):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(w.repo, "mark_alert_sent", broken_mark)
    assert w.dispatch(2) == AlertSummary(failed=1)

    monkeypatch.setattr(w.repo, "mark_alert_sent", real_mark)
    assert w.dispatch(3) == AlertSummary(sent=1)
    assert w.subjects == ["[DOWN] Shop", "[DOWN] Shop"]
    assert w.incident.down_alert_sent_at == T0 + timedelta(minutes=3)


def test_alerts_enabled_while_an_incident_is_open_sends_down():
    w = World(alerts_enabled=False)
    w.go_down()
    assert w.dispatch(2) == AlertSummary()
    update_monitor(w.repo, w.catalog, w.monitor.id, {"alerts_enabled": True},
                   check_url=lambda url: url, now=T0 + timedelta(minutes=10))
    assert w.dispatch(10) == AlertSummary(sent=1)
    assert w.subjects == ["[DOWN] Shop"]


def test_newline_in_a_stored_name_never_reaches_the_subject():
    w = World()
    w.monitor = w.repo.change_monitor(w.monitor.id, {"name": "Shop\r\nBcc: x@evil.test"},
                                      now=T0)
    w.go_down()
    w.dispatch(2)
    assert w.subjects == ["[DOWN] Shop Bcc: x@evil.test"]


# ── Backoff, log-once, limits, lock ──────────────────────────────


def test_backoff_blocks_until_the_retry_time_and_clears_on_success():
    backoff, key, t = AlertBackoff(timedelta(minutes=5)), (1, AlertKind.DOWN), T0
    assert not backoff.blocked(key, t)
    backoff.failed(key, t)
    assert backoff.blocked(key, t + timedelta(minutes=4, seconds=59))
    assert not backoff.blocked(key, t + timedelta(minutes=5))
    assert not backoff.blocked((1, AlertKind.RECOVERED), t)
    backoff.succeeded(key)
    assert not backoff.blocked(key, t)


def test_failed_send_is_not_retried_until_the_backoff_passes():
    w = World()
    w.go_down()
    backoff, failing = AlertBackoff(), FailingNotifier()
    assert w.dispatch(2, failing, backoff) == AlertSummary(failed=1)

    assert w.dispatch(3, backoff=backoff) == AlertSummary()  # backing off: not even tried
    assert w.dispatch(6.9, failing, backoff) == AlertSummary()
    assert failing.attempts == 1

    assert w.dispatch(7, backoff=backoff) == AlertSummary(sent=1)
    assert w.subjects == ["[DOWN] Shop"]


def test_no_recipients_warning_is_logged_once_per_alert(caplog):
    w = World(Settings.from_env({}))
    w.go_down()
    backoff = AlertBackoff()
    with caplog.at_level(logging.WARNING):
        for minute in (2, 8, 14):  # each retry waits out the backoff
            assert w.dispatch(minute, backoff=backoff) == AlertSummary(failed=1)
    assert caplog.text.count("No recipients") == 1


def test_at_most_max_alerts_per_run_the_rest_are_deferred(monkeypatch, caplog):
    monkeypatch.setattr(uptime_alerts, "MAX_ALERTS_PER_RUN", 2)
    w = World()
    for name in ("Blog", "Api"):
        other = w.repo.add_monitor(Monitor(id=0, name=name, url=f"https://{name}.test",
                                           interval_seconds=60, next_check_at=T0))
        w.repo.record_checks([CheckResult(other.id, T0, False)] * 3, evaluate)
    w.go_down()

    with caplog.at_level(logging.WARNING):
        assert w.dispatch(2) == AlertSummary(sent=2, deferred=1)
    assert "1 deferred to the next run" in caplog.text
    assert w.dispatch(3) == AlertSummary(sent=1)
    assert len(w.subjects) == 3


def test_dispatch_is_skipped_when_another_worker_holds_the_lock(monkeypatch):
    w = World()
    w.go_down()
    monkeypatch.setattr(w.repo, "alert_dispatch_lock", lambda: nullcontext(False))
    assert w.dispatch(2) == AlertSummary()
    assert w.subjects == []
    assert w.incident.down_alert_sent_at is None


def test_warns_once_before_unsent_recovery_ages_out(caplog):
    w = World()
    w.go_down()
    w.dispatch(2)
    w.check(10, up=True)  # closed at T0 + 10 min; RECOVERED now owed
    backoff, failing = AlertBackoff(), FailingNotifier()
    w.dispatch(11, failing, backoff)
    assert "dropped" not in caplog.text

    with caplog.at_level(logging.WARNING):
        w.dispatch(10 + 23 * 60, failing, backoff)
        w.dispatch(10 + 23 * 60 + 10, failing, backoff)
    assert caplog.text.count("they are dropped 24 h after it closed") == 1


def test_no_expiry_warning_for_a_silent_blip(caplog):
    w = World(alert_delay_minutes=5)
    w.go_down()
    w.check(3, up=True)  # shorter than the delay: nothing is owed
    with caplog.at_level(logging.WARNING):
        w.dispatch(3 + 23 * 60 + 30)
    assert "dropped" not in caplog.text


# ── Test alert ───────────────────────────────────────────────────


def test_send_test_alert_goes_to_all_recipients_and_records_nothing():
    w = World()
    w.catalog.add_alert_rule(AlertRule(id=0, target="rule@acme.test", on_down=False,
                                       on_recovery=False))
    to = send_test_alert(w.repo, w.catalog, w.notifier, w.monitor.id, now=T0,
                         settings_like=w.settings)
    assert to == [OPS, "rule@acme.test"]
    assert w.subjects == ["[TEST] Shop"]
    assert w.repo.list_incidents(w.monitor.id) == []


def test_send_test_alert_errors():
    w = World(Settings.from_env({}))
    with pytest.raises(MonitorNotFound):
        send_test_alert(w.repo, w.catalog, w.notifier, 99, now=T0, settings_like=w.settings)
    with pytest.raises(NoAlertRecipients):
        send_test_alert(w.repo, w.catalog, w.notifier, w.monitor.id, now=T0,
                        settings_like=w.settings)
    w.settings = _settings()
    with pytest.raises(AlertNotSent):
        send_test_alert(w.repo, w.catalog, FailingNotifier(), w.monitor.id, now=T0,
                        settings_like=w.settings)


def test_alert_kinds_are_logged(caplog):
    w = World()
    w.go_down()
    with caplog.at_level(logging.INFO, logger="ssl_monitor.uptime.alerts"):
        w.dispatch(2)
    assert "Uptime alert sent: DOWN" in caplog.text
    assert "1 sent, 0 failed" in caplog.text


# ── Failure handling never starves or spams ─────────────────────


def _down_monitor(w: World, name: str, minute: float = 0) -> Monitor:
    monitor = w.repo.add_monitor(Monitor(id=0, name=name, url=f"https://{name}.test",
                                         interval_seconds=60, next_check_at=T0))
    w.repo.record_checks(
        [CheckResult(monitor.id, T0 + timedelta(minutes=minute + i), False) for i in range(3)],
        evaluate,
    )
    return monitor


def test_incidents_without_recipients_do_not_starve_newer_alerts():
    """Three old outages nobody is subscribed to must not use up the failure budget."""
    w = World(Settings.from_env({}))  # no default recipients
    w.go_down()  # "Shop": oldest, nobody to email
    _down_monitor(w, "Blog", 1)
    _down_monitor(w, "Api", 2)
    watched = _down_monitor(w, "Pay", 3)
    w.catalog.add_alert_rule(AlertRule(id=0, target="pay@acme.test", monitor_id=watched.id))

    assert w.dispatch(6) == AlertSummary(sent=1, failed=3)
    assert w.subjects == ["[DOWN] Pay"]


def test_no_recipients_backs_off_instead_of_retrying_every_tick():
    w = World(Settings.from_env({}))
    w.go_down()
    backoff = AlertBackoff()
    assert w.dispatch(2, backoff=backoff) == AlertSummary(failed=1)
    assert w.dispatch(2.2, backoff=backoff) == AlertSummary()  # next tick: waiting

    w.catalog.add_alert_rule(AlertRule(id=0, target="late@acme.test"))
    assert w.dispatch(7, backoff=backoff) == AlertSummary(sent=1)  # rule added meanwhile


def test_mark_failing_after_a_send_backs_off_before_resending(monkeypatch):
    """The provider took the email but recording it failed: never resend every tick."""
    w = World()
    w.go_down()
    backoff = AlertBackoff()
    real_mark = w.repo.mark_alert_sent
    monkeypatch.setattr(w.repo, "mark_alert_sent",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("db hiccup")))
    assert w.dispatch(2, backoff=backoff) == AlertSummary(failed=1)
    assert w.dispatch(2.2, backoff=backoff) == AlertSummary()
    assert w.subjects == ["[DOWN] Shop"]

    monkeypatch.setattr(w.repo, "mark_alert_sent", real_mark)
    assert w.dispatch(7.5, backoff=backoff) == AlertSummary(sent=1)
    assert w.subjects == ["[DOWN] Shop", "[DOWN] Shop"]  # at-least-once, once per backoff


def test_unexpected_errors_count_towards_stopping_the_run(monkeypatch):
    """With the database failing, the run stops after a few errors instead of mailing
    every incident and failing to record each one."""
    w = World()
    w.go_down()
    for i, name in enumerate(("Blog", "Api", "Pay"), start=1):
        _down_monitor(w, name, i)
    monkeypatch.setattr(w.repo, "mark_alert_sent",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("db down")))

    summary = w.dispatch(6)

    assert summary == AlertSummary(failed=uptime_alerts.MAX_CONSECUTIVE_FAILURES, deferred=1)
    assert len(w.subjects) == uptime_alerts.MAX_CONSECUTIVE_FAILURES


# ── Re-enabling alerts ───────────────────────────────────────────


def _enable_alerts(w: World, minute: float) -> None:
    update_monitor(w.repo, w.catalog, w.monitor.id, {"alerts_enabled": True},
                   check_url=lambda url: url, now=T0 + timedelta(minutes=minute))


def test_outage_that_ended_while_alerts_were_off_stays_silent():
    w = World(alerts_enabled=False)
    w.go_down()
    w.check(5, up=True)  # recovered while alerts were off
    _enable_alerts(w, 60)

    assert w.dispatch(60) == AlertSummary()
    assert w.subjects == []


def test_enabling_alerts_records_when():
    w = World(alerts_enabled=False)
    _enable_alerts(w, 60)
    assert w.repo.get_monitor(w.monitor.id).alerts_enabled_at == T0 + timedelta(minutes=60)


def test_outage_still_open_when_alerts_return_gets_down_then_recovered():
    w = World(alerts_enabled=False)
    w.go_down()
    _enable_alerts(w, 10)
    w.check(12, up=True)  # recovered after alerts came back, before any dispatch
    assert w.dispatch(12) == AlertSummary(sent=2)
    assert [s.split("]")[0] for s in w.subjects] == ["[DOWN", "[RECOVERED after 12m"]
