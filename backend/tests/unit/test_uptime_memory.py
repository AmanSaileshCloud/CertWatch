"""MemoryUptimeRepo: same behaviour as the Postgres adapter, without a database."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from backend.ssl_monitor.adapters.storage.base import UptimeRepoPort
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.core.alert_models import AlertKind
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.core.uptime_models import (
    CheckResult,
    CloseReason,
    Incident,
    IncidentEvent,
    Monitor,
    MonitorState,
    MonitorTransition,
    MonitorType,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _monitor(name: str = "site", *, due_in: int = -1, interval: int = 60, **overrides) -> Monitor:
    return Monitor(
        id=0,
        name=name,
        url=f"https://{name}.example.com",
        interval_seconds=interval,
        next_check_at=NOW + dt.timedelta(seconds=due_in),
        **overrides,
    )


def _result(monitor_id: int, *, seconds: int = 0, **overrides) -> CheckResult:
    values = {"is_up": True, "status_code": 200, "response_ms": 120} | overrides
    return CheckResult(monitor_id=monitor_id, checked_at=NOW + dt.timedelta(seconds=seconds),
                       **values)


def _up(result: CheckResult) -> MonitorTransition:
    return MonitorTransition(result=result, state=MonitorState.UP, consecutive_failures=0,
                             consecutive_successes=1, first_failure_at=None)


def _went_down(monitor_id: int, *, seconds: int = 0, started: int = -120,
               error: str = "HTTP 503") -> MonitorTransition:
    result = _result(monitor_id, seconds=seconds, is_up=False, status_code=503, error=error)
    return MonitorTransition(result=result, state=MonitorState.DOWN, consecutive_failures=3,
                             consecutive_successes=0,
                             first_failure_at=NOW + dt.timedelta(seconds=started),
                             event=IncidentEvent.WENT_DOWN)


def _recovered(monitor_id: int, *, seconds: int = 0) -> MonitorTransition:
    return MonitorTransition(result=_result(monitor_id, seconds=seconds), state=MonitorState.UP,
                             consecutive_failures=0, consecutive_successes=2,
                             first_failure_at=None, event=IncidentEvent.RECOVERED)


def _record(repo, transitions: list[MonitorTransition]) -> list[Incident]:
    """Save prepared transitions through ``record_checks`` (rules return them as-is)."""
    prepared = {id(t.result): t for t in transitions}
    return repo.record_checks([t.result for t in transitions],
                              lambda monitor, result: prepared[id(result)])


def test_repo_satisfies_port():
    assert isinstance(MemoryUptimeRepo(), UptimeRepoPort)


def test_add_assigns_incrementing_ids_and_defaults():
    repo = MemoryUptimeRepo()
    first = repo.add_monitor(Monitor(id=999, name="a", url="https://a", interval_seconds=60))
    second = repo.add_monitor(_monitor("b"))

    assert (first.id, second.id) == (1, 2)
    assert first.type is MonitorType.HTTP and first.state is MonitorState.UNKNOWN
    assert first.next_check_at is not None and first.next_check_at.tzinfo is not None
    assert repo.get_monitor(1) == first
    assert repo.get_monitor(3) is None


def test_claim_returns_only_due_unpaused_oldest_first():
    repo = MemoryUptimeRepo()
    newer = repo.add_monitor(_monitor("newer", due_in=-10))
    older = repo.add_monitor(_monitor("older", due_in=-100))
    repo.add_monitor(_monitor("future", due_in=60))
    repo.add_monitor(_monitor("paused", due_in=-500, is_paused=True))

    claimed = repo.claim_due_monitors(NOW, limit=10)

    assert [m.id for m in claimed] == [older.id, newer.id]
    assert claimed[0].next_check_at == older.next_check_at


def test_claim_honours_limit():
    repo = MemoryUptimeRepo()
    ids = [repo.add_monitor(_monitor(f"m{i}", due_in=-100 + i)).id for i in range(5)]
    assert [m.id for m in repo.claim_due_monitors(NOW, limit=2)] == ids[:2]
    assert [m.id for m in repo.claim_due_monitors(NOW, limit=10)] == ids[2:]


@pytest.mark.parametrize("interval", [60, 300, 600])
def test_claim_keeps_cadence_anchored_to_due_time(interval):
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor(interval=interval, due_in=-1))
    repo.claim_due_monitors(NOW, limit=10)
    due_at = NOW - dt.timedelta(seconds=1)
    assert repo.get_monitor(added.id).next_check_at == due_at + dt.timedelta(seconds=interval)


def test_claim_far_behind_schedule_restarts_from_now():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor(interval=60, due_in=-600))
    repo.claim_due_monitors(NOW, limit=10)
    assert repo.get_monitor(added.id).next_check_at == NOW + dt.timedelta(seconds=60)


def test_second_claim_at_same_time_returns_nothing():
    repo = MemoryUptimeRepo()
    repo.add_monitor(_monitor())
    assert len(repo.claim_due_monitors(NOW, limit=10)) == 1
    assert repo.claim_due_monitors(NOW, limit=10) == []


def test_record_checks_persists_last_fields_and_transition_state():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    first_failure = NOW - dt.timedelta(seconds=60)
    result = _result(added.id, is_up=False, status_code=503, response_ms=900, error="HTTP 503")

    returned = _record(repo, [MonitorTransition(
        result=result, state=MonitorState.UNKNOWN, consecutive_failures=2,
        consecutive_successes=0, first_failure_at=first_failure)])

    after = repo.get_monitor(added.id)
    assert returned == []
    assert (after.last_checked_at, after.last_status_code, after.last_response_ms,
            after.last_error) == (NOW, 503, 900, "HTTP 503")
    assert (after.state, after.consecutive_failures, after.consecutive_successes,
            after.first_failure_at) == (MonitorState.UNKNOWN, 2, 0, first_failure)
    assert after.next_check_at == added.next_check_at
    assert repo.list_check_results(added.id) == [result]


def test_record_checks_clears_first_failure_and_counts_successes():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    _record(repo, [_went_down(added.id)])
    _record(repo, [_recovered(added.id, seconds=5)])

    after = repo.get_monitor(added.id)
    assert (after.state, after.consecutive_failures, after.consecutive_successes,
            after.first_failure_at) == (MonitorState.UP, 0, 2, None)


def test_went_down_opens_incident():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())

    opened = _record(repo, [_went_down(added.id, started=-120, error="timeout")])

    assert opened == [Incident(id=1, monitor_id=added.id,
                               started_at=NOW - dt.timedelta(seconds=120), reason="timeout")]
    assert repo.list_incidents() == opened


def test_went_down_without_first_failure_starts_at_checked_at():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())

    [opened] = _record(repo, [replace(_went_down(added.id, seconds=7),
                                           first_failure_at=None)])

    assert opened.started_at == NOW + dt.timedelta(seconds=7)


def test_second_went_down_while_open_opens_nothing():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    [first] = _record(repo, [_went_down(added.id)])

    assert _record(repo, [_went_down(added.id, seconds=60, error="other")]) == []
    assert repo.list_incidents() == [first]


def test_recovered_closes_open_incident():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    [opened] = _record(repo, [_went_down(added.id, started=-120)])

    closed = _record(repo, [_recovered(added.id, seconds=5)])

    expected = replace(opened, resolved_at=NOW + dt.timedelta(seconds=5), duration_seconds=125,
                       close_reason=CloseReason.RECOVERED)
    assert closed == [expected]
    assert repo.list_incidents() == [expected]


def test_recovered_without_open_incident_is_noop():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    assert _record(repo, [_recovered(added.id)]) == []
    assert repo.list_incidents() == []


def test_went_down_after_recovery_opens_new_incident():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    _record(repo, [_went_down(added.id, started=-120)])
    _record(repo, [_recovered(added.id)])

    [reopened] = _record(repo, [_went_down(added.id, seconds=300, started=200)])

    assert reopened.id == 2 and reopened.is_open
    assert [i.id for i in repo.list_incidents(added.id)] == [2, 1]


def test_incident_changes_return_in_transition_order():
    repo = MemoryUptimeRepo()
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_went_down(a.id)])

    changed = _record(repo, [_went_down(b.id), _up(_result(a.id)),
                                  _recovered(a.id, seconds=1)])

    assert [(i.monitor_id, i.is_open) for i in changed] == [(b.id, True), (a.id, False)]


def test_transitions_for_unknown_monitor_are_dropped():
    repo = MemoryUptimeRepo()
    kept = repo.add_monitor(_monitor())

    opened = _record(repo, [_went_down(404), _up(_result(kept.id))])

    assert opened == []
    assert repo.list_check_results(404) == []
    assert repo.list_check_results(kept.id) == [_result(kept.id)]
    assert repo.list_incidents() == []


def test_list_incidents_newest_first_with_filter_and_limit():
    repo = MemoryUptimeRepo()
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_went_down(a.id, started=-300)])
    _record(repo, [_recovered(a.id)])
    _record(repo, [_went_down(a.id, started=-100), _went_down(b.id, started=-200)])

    assert [(i.monitor_id, i.started_at) for i in repo.list_incidents()] == [
        (a.id, NOW - dt.timedelta(seconds=100)),
        (b.id, NOW - dt.timedelta(seconds=200)),
        (a.id, NOW - dt.timedelta(seconds=300)),
    ]
    assert [i.id for i in repo.list_incidents(a.id)] == [2, 1]
    assert [i.id for i in repo.list_incidents(limit=1)] == [2]
    assert repo.list_incidents(monitor_id=999) == []


def test_list_incidents_ties_break_on_newest_id():
    repo = MemoryUptimeRepo()
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_went_down(a.id), _went_down(b.id)])
    assert [i.monitor_id for i in repo.list_incidents()] == [b.id, a.id]


def test_list_check_results_newest_first_with_limit():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    _record(repo, [_up(_result(added.id, seconds=s)) for s in (10, 30, 20)])

    listed = repo.list_check_results(added.id, limit=2)

    assert [r.checked_at for r in listed] == [NOW + dt.timedelta(seconds=s) for s in (30, 20)]


def test_result_for_paused_monitor_is_dropped():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor(is_paused=True))
    failed = _result(added.id, is_up=False, error="HTTP 503")

    assert repo.record_checks([failed], evaluate) == []
    assert repo.list_check_results(added.id) == []


# ── User changes, listing, deletion and uptime counts ──


def test_list_check_results_since_is_inclusive():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    _record(repo, [_up(_result(added.id, seconds=s)) for s in (0, 10, 20)])

    listed = repo.list_check_results(added.id, since=NOW + dt.timedelta(seconds=10))

    assert [r.checked_at for r in listed] == [NOW + dt.timedelta(seconds=s) for s in (20, 10)]


def test_list_monitors_orders_by_name_then_id():
    repo = MemoryUptimeRepo()
    b = repo.add_monitor(_monitor("b"))
    a_first = repo.add_monitor(_monitor("a"))
    a_second = repo.add_monitor(_monitor("a"))
    assert [m.id for m in repo.list_monitors()] == [a_first.id, a_second.id, b.id]


def test_change_monitor_applies_values_and_updated_at():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    later = NOW + dt.timedelta(hours=1)

    changed = repo.change_monitor(added.id, {"name": "renamed", "interval_seconds": 300,
                                             "keyword": None, "type": "http",
                                             "next_check_at": later}, now=later)

    assert (changed.name, changed.interval_seconds, changed.next_check_at,
            changed.updated_at, changed.type) == ("renamed", 300, later, later, MonitorType.HTTP)
    assert repo.get_monitor(added.id) == changed


@pytest.mark.parametrize("field", ["state", "id", "bogus"])
def test_change_monitor_rejects_non_editable_fields(field):
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    with pytest.raises(ValueError, match=field):
        repo.change_monitor(added.id, {field: 1}, now=NOW)
    assert repo.get_monitor(added.id) == added


def test_change_monitor_missing_returns_none():
    assert MemoryUptimeRepo().change_monitor(42, {"name": "x"}, now=NOW) is None


def test_change_monitor_reset_state():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    _record(repo, [_went_down(added.id)])

    changed = repo.change_monitor(added.id, {}, now=NOW, reset_state=True)

    assert (changed.state, changed.consecutive_failures, changed.consecutive_successes,
            changed.first_failure_at) == (MonitorState.UNKNOWN, 0, 0, None)
    assert changed.last_error == "HTTP 503"


def test_change_monitor_closes_open_incident_with_reason():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    [opened] = _record(repo, [_went_down(added.id, started=-120)])
    at = NOW + dt.timedelta(seconds=30, milliseconds=900)

    changed = repo.change_monitor(added.id, {"is_paused": True}, now=at, reset_state=True,
                                  close_reason=CloseReason.PAUSED)

    assert changed.is_paused and changed.state is MonitorState.UNKNOWN
    assert repo.list_incidents(added.id) == [replace(
        opened, resolved_at=at, duration_seconds=150, close_reason=CloseReason.PAUSED)]


def test_change_monitor_close_without_open_incident_is_fine():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    changed = repo.change_monitor(added.id, {"url": "https://new.example.com"}, now=NOW,
                                  close_reason=CloseReason.EDITED)
    assert changed.url == "https://new.example.com"
    assert repo.list_incidents() == []


def test_results_after_pause_via_change_monitor_are_dropped():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    repo.claim_due_monitors(NOW, limit=10)
    repo.change_monitor(added.id, {"is_paused": True}, now=NOW, reset_state=True,
                        close_reason=CloseReason.PAUSED)

    assert repo.record_checks([_result(added.id, is_up=False)], evaluate) == []
    assert repo.list_check_results(added.id) == []


def test_delete_monitor_removes_results_and_incidents():
    repo = MemoryUptimeRepo()
    gone = repo.add_monitor(_monitor("gone"))
    kept = repo.add_monitor(_monitor("kept"))
    _record(repo, [_went_down(gone.id), _up(_result(kept.id))])

    assert repo.delete_monitor(gone.id) is True
    assert repo.delete_monitor(gone.id) is False

    assert repo.get_monitor(gone.id) is None
    assert repo.list_incidents() == []
    assert repo.list_check_results(gone.id) == []
    assert repo.uptime_counts(NOW) == {kept.id: (1, 1)}


def test_uptime_counts_per_monitor_since():
    repo = MemoryUptimeRepo()
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    old = repo.add_monitor(_monitor("old"))
    _record(repo, [_up(_result(a.id, seconds=0)), _up(_result(a.id, seconds=10)),
                   _up(_result(a.id, seconds=20, is_up=False)),
                   _up(_result(b.id, seconds=30, is_up=False)),
                   _up(_result(a.id, seconds=-60)), _up(_result(old.id, seconds=-60))])

    assert repo.uptime_counts(NOW) == {a.id: (3, 2), b.id: (1, 0)}
    assert repo.uptime_counts(NOW + dt.timedelta(hours=1)) == {}


def test_uptime_counts_only_for_given_monitors():
    repo = MemoryUptimeRepo()
    a = repo.add_monitor(_monitor("a"))
    b = repo.add_monitor(_monitor("b"))
    _record(repo, [_up(_result(a.id)), _up(_result(b.id, is_up=False))])

    assert repo.uptime_counts(NOW, monitor_ids=[b.id]) == {b.id: (1, 0)}
    assert repo.uptime_counts(NOW, monitor_ids=[]) == {}
    assert repo.uptime_counts(NOW, monitor_ids=None) == {a.id: (1, 1), b.id: (1, 0)}


def _failed(monitor: Monitor) -> CheckResult:
    return replace(_result(monitor.id, is_up=False, status_code=503, error="HTTP 503"),
                   monitor_version=monitor.updated_at)


def test_result_for_monitor_edited_during_the_check_is_dropped():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    [claimed] = repo.claim_due_monitors(NOW, limit=10)
    repo.change_monitor(added.id, {"name": "renamed"}, now=NOW + dt.timedelta(seconds=1))

    assert repo.record_checks([_failed(claimed)], evaluate) == []
    assert repo.list_check_results(added.id) == []
    assert repo.get_monitor(added.id).consecutive_failures == 0


def test_result_with_current_version_is_saved_without_the_version():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor())
    [claimed] = repo.claim_due_monitors(NOW, limit=10)

    repo.record_checks([_failed(claimed)], evaluate)

    assert repo.get_monitor(added.id).consecutive_failures == 1
    assert repo.list_check_results(added.id)[0].monitor_version is None


# --- Step 4: alert fields, alert candidates, mark_alert_sent --------------------------

LATER = NOW + dt.timedelta(hours=1)


def _open(repo, name: str = "site", *, started: int = -120, **monitor_fields):
    monitor = repo.add_monitor(_monitor(name, **monitor_fields))
    [incident] = _record(repo, [_went_down(monitor.id, started=started)])
    return monitor, incident


def _closed(repo, name: str = "site", *, started: int = -120, **monitor_fields):
    monitor, _ = _open(repo, name, started=started, **monitor_fields)
    [incident] = _record(repo, [_recovered(monitor.id, seconds=60)])
    return monitor, incident


def _candidate_ids(repo, now=LATER) -> list[int]:
    return [c.incident.id for c in repo.alert_candidates(now)]


def test_new_monitor_alert_fields_default_and_round_trip():
    repo = MemoryUptimeRepo()
    plain = repo.add_monitor(_monitor("plain"))
    tuned = repo.add_monitor(_monitor("tuned", alerts_enabled=False, reminder_minutes=60,
                                      alert_delay_minutes=5))

    assert (plain.alerts_enabled, plain.reminder_minutes, plain.alert_delay_minutes) == (
        True, None, None)
    assert (tuned.alerts_enabled, tuned.reminder_minutes, tuned.alert_delay_minutes) == (
        False, 60, 5)
    assert repo.get_monitor(tuned.id) == tuned


def test_change_monitor_sets_alert_fields():
    repo = MemoryUptimeRepo()
    added = repo.add_monitor(_monitor(reminder_minutes=60))

    changed = repo.change_monitor(added.id, {"alerts_enabled": False, "reminder_minutes": None,
                                             "alert_delay_minutes": 30}, now=NOW)

    assert (changed.alerts_enabled, changed.reminder_minutes, changed.alert_delay_minutes) == (
        False, None, 30)
    assert repo.get_monitor(added.id) == changed


def test_new_incident_has_no_reminders():
    _, incident = _open(MemoryUptimeRepo())
    assert (incident.last_reminder_sent_at, incident.reminder_count) == (None, 0)


def test_alert_candidates_are_open_and_unnotified_recently_closed_oldest_first():
    repo = MemoryUptimeRepo()
    _, still_down = _open(repo, "down", started=-60)
    _, recovered = _closed(repo, "back", started=-600)
    _, tie = _open(repo, "tie", started=-60)

    assert _candidate_ids(repo) == [recovered.id, still_down.id, tie.id]


def test_alert_candidates_carry_current_incident_and_monitor():
    repo = MemoryUptimeRepo()
    monitor, incident = _open(repo, reminder_minutes=15)
    repo.mark_alert_sent(incident.id, AlertKind.DOWN, NOW)

    [candidate] = repo.alert_candidates(LATER)

    assert candidate.monitor == repo.get_monitor(monitor.id)
    assert candidate.incident == repo.list_incidents(monitor.id)[0]
    assert candidate.incident.down_alert_sent_at == NOW


def test_alert_candidates_skip_closed_once_recovery_was_sent():
    repo = MemoryUptimeRepo()
    _, closed = _closed(repo)
    repo.mark_alert_sent(closed.id, AlertKind.RECOVERED, LATER)
    assert _candidate_ids(repo) == []


def test_alert_candidates_skip_closed_outside_the_window():
    repo = MemoryUptimeRepo()
    _, closed = _closed(repo, "back")
    _, still_down = _open(repo, "down")
    resolved = closed.resolved_at

    assert _candidate_ids(repo, resolved + dt.timedelta(hours=24)) == [closed.id, still_down.id]
    assert _candidate_ids(repo, resolved + dt.timedelta(hours=24, seconds=1)) == [still_down.id]


def test_alert_candidates_skip_monitors_with_alerts_disabled():
    repo = MemoryUptimeRepo()
    monitor, incident = _open(repo, alerts_enabled=False)
    assert _candidate_ids(repo) == []

    repo.change_monitor(monitor.id, {"alerts_enabled": True}, now=NOW)
    assert _candidate_ids(repo) == [incident.id]


def test_alert_candidates_names_default_to_none():
    repo = MemoryUptimeRepo()
    _open(repo, customer_id=1, group_id=2)
    [candidate] = repo.alert_candidates(LATER)
    assert (candidate.customer_name, candidate.group_name) == (None, None)


def test_alert_candidates_take_names_from_the_lookup():
    names = {1: ("Acme", "Web"), 2: ("Acme", None)}
    repo = MemoryUptimeRepo(name_lookup=lambda m: names.get(m.id, (None, None)))
    _open(repo, "grouped", started=-300)
    _open(repo, "customer", started=-200)
    _open(repo, "loose", started=-100)

    found = [(c.monitor.name, c.customer_name, c.group_name)
             for c in repo.alert_candidates(LATER)]

    assert found == [("grouped", "Acme", "Web"), ("customer", "Acme", None),
                     ("loose", None, None)]


def test_mark_alert_sent_records_each_kind():
    repo = MemoryUptimeRepo()
    monitor, incident = _open(repo)
    first, second = NOW + dt.timedelta(minutes=30), NOW + dt.timedelta(minutes=60)

    repo.mark_alert_sent(incident.id, AlertKind.DOWN, NOW)
    repo.mark_alert_sent(incident.id, AlertKind.REMINDER, first)
    repo.mark_alert_sent(incident.id, AlertKind.REMINDER, second)
    repo.mark_alert_sent(incident.id, AlertKind.RECOVERED, LATER)

    assert repo.list_incidents(monitor.id) == [replace(
        incident, down_alert_sent_at=NOW, last_reminder_sent_at=second, reminder_count=2,
        recovery_alert_sent_at=LATER)]


def test_mark_alert_sent_ignores_test_alerts_and_unknown_incidents():
    repo = MemoryUptimeRepo()
    monitor, incident = _open(repo)

    repo.mark_alert_sent(incident.id, AlertKind.TEST, NOW)
    repo.mark_alert_sent(999, AlertKind.DOWN, NOW)

    assert repo.list_incidents(monitor.id) == [incident]
