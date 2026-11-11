"""Unit tests for the pure incident rules (consecutive-failure / recovery logic)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.ssl_monitor.core.incident_rules import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_RECOVERY_THRESHOLD,
    apply_transition,
    evaluate,
    evaluate_in_order,
)
from backend.ssl_monitor.core.uptime_models import (
    CheckResult,
    IncidentEvent,
    Monitor,
    MonitorState,
    MonitorTransition,
)

MONITOR_ID = 7
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
STEP = timedelta(minutes=1)


def make_monitor(**overrides) -> Monitor:
    fields = dict(id=MONITOR_ID, name="Example", url="https://example.com", interval_seconds=60)
    fields.update(overrides)
    return Monitor(**fields)


def check(is_up: bool, minute: int = 0) -> CheckResult:
    return CheckResult(monitor_id=MONITOR_ID, checked_at=T0 + minute * STEP, is_up=is_up)


def run(
    outcomes: list[bool], monitor: Monitor | None = None, **thresholds
) -> tuple[Monitor, list[MonitorTransition]]:
    """Feed a sequence of up/down outcomes (one per minute) through evaluate."""
    monitor = monitor or make_monitor()
    transitions = []
    for minute, is_up in enumerate(outcomes):
        transition = evaluate(monitor, check(is_up, minute), **thresholds)
        transitions.append(transition)
        monitor = apply_transition(monitor, transition)
    return monitor, transitions


def events(transitions: list[MonitorTransition]) -> list[IncidentEvent]:
    return [t.event for t in transitions]


NONE = IncidentEvent.NONE
WENT_DOWN = IncidentEvent.WENT_DOWN
RECOVERED = IncidentEvent.RECOVERED


def test_defaults():
    assert DEFAULT_FAILURE_THRESHOLD == 3
    assert DEFAULT_RECOVERY_THRESHOLD == 1


def test_first_failure_counts_but_does_not_alert():
    t = evaluate(make_monitor(), check(False, 0))

    assert t.event is NONE
    assert t.state is MonitorState.UNKNOWN
    assert t.consecutive_failures == 1
    assert t.consecutive_successes == 0
    assert t.first_failure_at == T0


def test_second_failure_keeps_first_failure_time():
    monitor, transitions = run([False, False], make_monitor(state=MonitorState.UP))

    assert events(transitions) == [NONE, NONE]
    assert monitor.state is MonitorState.UP
    assert monitor.consecutive_failures == 2
    assert monitor.first_failure_at == T0


def test_third_failure_goes_down_from_first_failure_time():
    monitor, transitions = run([False, False, False])

    assert events(transitions) == [NONE, NONE, WENT_DOWN]
    assert monitor.state is MonitorState.DOWN
    assert monitor.consecutive_failures == 3
    assert transitions[-1].first_failure_at == T0


def test_further_failures_while_down_open_no_second_incident():
    monitor, transitions = run([False] * 6)

    assert events(transitions) == [NONE, NONE, WENT_DOWN, NONE, NONE, NONE]
    assert monitor.state is MonitorState.DOWN
    assert monitor.consecutive_failures == 6
    assert monitor.first_failure_at == T0


def test_success_before_threshold_resets_without_incident():
    monitor, transitions = run([False, False, True], make_monitor(state=MonitorState.UP))

    assert events(transitions) == [NONE, NONE, NONE]
    assert monitor.state is MonitorState.UP
    assert monitor.consecutive_failures == 0
    assert monitor.consecutive_successes == 1
    assert monitor.first_failure_at is None


def test_success_while_down_recovers():
    monitor, transitions = run([False, False, False, True])

    assert events(transitions) == [NONE, NONE, WENT_DOWN, RECOVERED]
    assert monitor.state is MonitorState.UP
    assert monitor.consecutive_failures == 0
    assert monitor.consecutive_successes == 1
    assert monitor.first_failure_at is None


def test_recovery_threshold_two_needs_two_successes():
    monitor, transitions = run([False, False, False, True], recovery_threshold=2)

    assert transitions[-1].event is NONE
    assert monitor.state is MonitorState.DOWN
    assert monitor.consecutive_successes == 1
    assert monitor.first_failure_at == T0

    t = evaluate(monitor, check(True, 4), recovery_threshold=2)
    assert t.event is RECOVERED
    assert t.state is MonitorState.UP
    assert t.first_failure_at is None


def test_failure_during_partial_recovery_keeps_original_outage_start():
    monitor, transitions = run([False, False, False, True, False], recovery_threshold=2)

    assert events(transitions) == [NONE, NONE, WENT_DOWN, NONE, NONE]
    assert monitor.state is MonitorState.DOWN
    assert monitor.consecutive_failures == 1
    assert monitor.consecutive_successes == 0
    assert monitor.first_failure_at == T0


def test_flapping_opens_exactly_one_incident_on_the_last_check():
    monitor, transitions = run([False, False, True, False, False, False])

    assert events(transitions) == [NONE, NONE, NONE, NONE, NONE, WENT_DOWN]
    assert transitions[-1].first_failure_at == T0 + 3 * STEP


def test_unknown_monitor_becomes_up_on_success():
    t = evaluate(make_monitor(), check(True))

    assert t.state is MonitorState.UP
    assert t.event is NONE
    assert t.consecutive_successes == 1
    assert t.first_failure_at is None


def test_failure_threshold_one_goes_down_immediately():
    t = evaluate(make_monitor(state=MonitorState.UP), check(False), failure_threshold=1)

    assert t.event is WENT_DOWN
    assert t.state is MonitorState.DOWN
    assert t.first_failure_at == T0


def test_custom_failure_threshold_five():
    monitor, transitions = run([False] * 5, failure_threshold=5)

    assert events(transitions) == [NONE, NONE, NONE, NONE, WENT_DOWN]
    assert monitor.state is MonitorState.DOWN


def test_inputs_are_not_mutated():
    monitor = make_monitor(state=MonitorState.UP, consecutive_failures=2, first_failure_at=T0)
    snapshot = replace(monitor)
    result = check(False, 2)

    evaluate(monitor, result)

    assert monitor == snapshot
    assert result == check(False, 2)


@pytest.mark.parametrize("name", ["failure_threshold", "recovery_threshold"])
@pytest.mark.parametrize("value", [0, -1])
def test_threshold_below_one_raises(name, value):
    with pytest.raises(ValueError, match=name):
        evaluate(make_monitor(), check(True), **{name: value})


def test_transition_carries_result_and_monitor_id():
    result = check(False)

    t = evaluate(make_monitor(), result)

    assert t.result is result
    assert t.monitor_id == MONITOR_ID


def test_evaluate_in_order_chains_results_for_the_same_monitor():
    monitor = make_monitor()
    results = [check(False, 0), check(False, 1), check(False, 2)]

    transitions = evaluate_in_order({monitor.id: monitor}, results, evaluate)

    assert [t.event for t in transitions] == [
        IncidentEvent.NONE, IncidentEvent.NONE, IncidentEvent.WENT_DOWN]
    assert transitions[-1].first_failure_at == results[0].checked_at


def test_evaluate_in_order_skips_unknown_monitors():
    assert evaluate_in_order({}, [check(False)], evaluate) == []
