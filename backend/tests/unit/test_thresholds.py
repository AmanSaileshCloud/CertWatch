"""Table-driven tests for the pure alert-decision logic, including idempotency
(no duplicate fire on repeated runs) and renewal reset."""

from __future__ import annotations

import pytest

from backend.ssl_monitor.core.thresholds import decide_alert

THRESHOLDS = [30, 14, 7, 1]


@pytest.mark.parametrize(
    "days_left, last_alert, expect_fire, expect_threshold, expect_new_last",
    [
        # Healthy: above all thresholds → no alert, dedup state cleared.
        (60, None, False, None, None),
        (31, None, False, None, None),
        # First crossing of each band fires at the most urgent crossed threshold.
        (30, None, True, 30, 30),
        (29, None, True, 30, 30),
        (14, None, True, 14, 14),
        (10, None, True, 14, 14),
        (7, None, True, 7, 7),
        (1, None, True, 1, 1),
        (0, None, True, 1, 1),
        # Expired crosses everything → most urgent threshold.
        (-5, None, True, 1, 1),
        # Idempotency: already alerted at this band → suppress, keep state.
        (29, 30, False, None, 30),
        (10, 14, False, None, 14),
        (5, 7, False, None, 7),
        # Escalation: a more urgent band fires even though we already alerted.
        (10, 30, True, 14, 14),
        (5, 14, True, 7, 7),
        (0, 7, True, 1, 1),
        # De-escalation can't happen naturally, but a stale less-urgent last value
        # must never re-fire a less urgent alert.
        (20, 7, False, None, 7),
        # Renewal: jumps back above all thresholds → reset to None.
        (90, 7, False, None, None),
        # Unreachable / no data: never alert, never touch dedup state.
        (None, None, False, None, None),
        (None, 14, False, None, 14),
    ],
)
def test_decide_alert(days_left, last_alert, expect_fire, expect_threshold, expect_new_last):
    decision = decide_alert(days_left, THRESHOLDS, last_alert)
    assert decision.should_alert is expect_fire
    assert decision.threshold == expect_threshold
    assert decision.new_last_alert == expect_new_last


def test_idempotent_across_repeated_runs():
    """Two checker passes at the same days-remaining fire exactly once."""
    last = None
    first = decide_alert(6, THRESHOLDS, last)
    assert first.should_alert is True
    last = first.new_last_alert

    second = decide_alert(6, THRESHOLDS, last)
    assert second.should_alert is False
    assert second.new_last_alert == last


def test_full_lifecycle_fires_once_per_band():
    """Walking a cert down to expiry fires once per crossed band, never twice."""
    last = None
    fired = []
    for day in [40, 30, 20, 14, 9, 7, 3, 1, 0, -2]:
        d = decide_alert(day, THRESHOLDS, last)
        if d.should_alert:
            fired.append(d.threshold)
        last = d.new_last_alert
    assert fired == [30, 14, 7, 1]


def test_threshold_order_independent():
    """Unsorted thresholds produce the same decision."""
    a = decide_alert(6, [1, 7, 14, 30], None)
    b = decide_alert(6, [30, 14, 7, 1], None)
    assert a == b
