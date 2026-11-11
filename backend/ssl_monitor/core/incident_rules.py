"""Pure incident rules: turn one check result into the monitor's next state.

A single failed check is not an outage — networks blip. A monitor is only
confirmed DOWN after ``failure_threshold`` failures in a row, and that moment
opens an incident (``WENT_DOWN``). While DOWN, further failures change nothing
(no second incident). A DOWN monitor is confirmed UP again after
``recovery_threshold`` successes in a row, which closes the incident
(``RECOVERED``).

``first_failure_at`` remembers when the current run of trouble began, so the
incident start is the first failed check, not the one that crossed the
threshold. It is kept for as long as an incident is open and cleared once the
monitor is healthy.

No I/O and no clock: the time comes from the check result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from .uptime_models import (
    CheckResult,
    IncidentEvent,
    Monitor,
    MonitorState,
    MonitorTransition,
)

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_RECOVERY_THRESHOLD = 1
MIN_THRESHOLD = 1


def evaluate(
    monitor: Monitor,
    result: CheckResult,
    *,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    recovery_threshold: int = DEFAULT_RECOVERY_THRESHOLD,
) -> MonitorTransition:
    """Apply one check result to a monitor and return the resulting transition."""
    _require_valid_threshold("failure_threshold", failure_threshold)
    _require_valid_threshold("recovery_threshold", recovery_threshold)

    if result.is_up:
        return _on_success(monitor, result, recovery_threshold)
    return _on_failure(monitor, result, failure_threshold)


def apply_transition(monitor: Monitor, transition: MonitorTransition) -> Monitor:
    """The monitor as it is after ``transition`` (state, counters, last check)."""
    result = transition.result
    return replace(
        monitor,
        state=transition.state,
        consecutive_failures=transition.consecutive_failures,
        consecutive_successes=transition.consecutive_successes,
        first_failure_at=transition.first_failure_at,
        last_checked_at=result.checked_at,
        last_status_code=result.status_code,
        last_response_ms=result.response_ms,
        last_error=result.error,
    )


def evaluate_in_order(
    current: Mapping[int, Monitor],
    results: Sequence[CheckResult],
    rules: Callable[[Monitor, CheckResult], MonitorTransition],
) -> list[MonitorTransition]:
    """Apply ``rules`` to each result in order, starting from each monitor's
    ``current`` state; results for monitors missing from ``current``, and stale
    results (see :func:`is_stale`), are skipped."""
    state = dict(current)
    transitions = []
    for result in results:
        monitor = state.get(result.monitor_id)
        if monitor is None or is_stale(monitor, result):
            continue
        transition = rules(monitor, result)
        state[result.monitor_id] = apply_transition(monitor, transition)
        transitions.append(transition)
    return transitions


def is_stale(monitor: Monitor, result: CheckResult) -> bool:
    """True if ``monitor`` was changed after the check that produced ``result`` began."""
    return result.monitor_version is not None and result.monitor_version != monitor.updated_at


def _require_valid_threshold(name: str, value: int) -> None:
    if value < MIN_THRESHOLD:
        raise ValueError(f"{name} must be >= {MIN_THRESHOLD}, got {value}")


def _on_failure(
    monitor: Monitor, result: CheckResult, failure_threshold: int
) -> MonitorTransition:
    failures = monitor.consecutive_failures + 1
    first_failure_at = monitor.first_failure_at or result.checked_at
    goes_down = monitor.state != MonitorState.DOWN and failures >= failure_threshold

    return MonitorTransition(
        result=result,
        state=MonitorState.DOWN if goes_down else monitor.state,
        consecutive_failures=failures,
        consecutive_successes=0,
        first_failure_at=first_failure_at,
        event=IncidentEvent.WENT_DOWN if goes_down else IncidentEvent.NONE,
    )


def _on_success(
    monitor: Monitor, result: CheckResult, recovery_threshold: int
) -> MonitorTransition:
    successes = monitor.consecutive_successes + 1

    if monitor.state == MonitorState.DOWN and successes < recovery_threshold:
        return _success_transition(
            result, MonitorState.DOWN, successes, monitor.first_failure_at, IncidentEvent.NONE
        )

    event = IncidentEvent.RECOVERED if monitor.state == MonitorState.DOWN else IncidentEvent.NONE
    return _success_transition(result, MonitorState.UP, successes, None, event)


def _success_transition(
    result: CheckResult,
    state: MonitorState,
    successes: int,
    first_failure_at: datetime | None,
    event: IncidentEvent,
) -> MonitorTransition:
    return MonitorTransition(
        result=result,
        state=state,
        consecutive_failures=0,
        consecutive_successes=successes,
        first_failure_at=first_failure_at,
        event=event,
    )
