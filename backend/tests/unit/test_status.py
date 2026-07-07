"""Boundary tests for status classification and the days-remaining calc."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.ssl_monitor.core.models import Status
from backend.ssl_monitor.core.status import classify_status, days_remaining

THRESHOLDS = [30, 14, 7, 1]
UTC = dt.timezone.utc


@pytest.mark.parametrize(
    "reachable, days_left, expected",
    [
        (False, None, Status.UNREACHABLE),
        (False, 5, Status.UNREACHABLE),   # unreachable wins even if a stale value exists
        (True, None, Status.UNREACHABLE),  # reachable but unparsable → unreachable
        (True, -1, Status.EXPIRED),
        (True, -100, Status.EXPIRED),
        (True, 0, Status.WARNING),         # expires today, not yet expired
        (True, 7, Status.WARNING),
        (True, 30, Status.WARNING),        # boundary: == max threshold → warning
        (True, 31, Status.OK),             # boundary: just above → ok
        (True, 365, Status.OK),
    ],
)
def test_classify_status(reachable, days_left, expected):
    assert classify_status(reachable, days_left, THRESHOLDS) is expected


def test_unreachable_is_never_expiring():
    """A down host must classify as unreachable, never warning/expired."""
    status = classify_status(False, None, THRESHOLDS)
    assert status is Status.UNREACHABLE
    assert status not in (Status.WARNING, Status.EXPIRED)


def test_days_remaining_floor_behavior():
    now = dt.datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)
    # ~12 hours left → still 0 days (not yet expired)
    assert days_remaining(now + dt.timedelta(hours=12), now) == 0
    # expired ~12 hours ago → -1
    assert days_remaining(now - dt.timedelta(hours=12), now) == -1
    # exactly 10 days
    assert days_remaining(now + dt.timedelta(days=10), now) == 10


def test_days_remaining_requires_aware_datetimes():
    naive = dt.datetime(2026, 6, 26, 12, 0, 0)
    aware = naive.replace(tzinfo=UTC)
    with pytest.raises(ValueError):
        days_remaining(naive, aware)
    with pytest.raises(ValueError):
        days_remaining(aware, naive)
