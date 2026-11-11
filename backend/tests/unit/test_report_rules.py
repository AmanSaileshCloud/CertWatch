"""core.reports: periods in IST, downtime clipping and uptime %."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from backend.ssl_monitor.core.reports import (
    Granularity,
    downtime_seconds,
    monitored_seconds,
    period_key,
    periods,
    range_problem,
    uptime_percent,
)
from backend.ssl_monitor.core.uptime_models import Incident

UTC = dt.timezone.utc
IST = ZoneInfo("Asia/Kolkata")
D = dt.date


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


def test_daily_periods_start_at_ist_midnight() -> None:
    days = periods(D(2026, 11, 1), D(2026, 11, 2), Granularity.DAY, IST)
    assert [p.label for p in days] == ["2026-11-01", "2026-11-02"]
    # Midnight IST is 18:30 UTC the evening before.
    assert days[0].start == _utc(2026, 10, 31, 18, 30)
    assert days[0].end == days[1].start == _utc(2026, 11, 1, 18, 30)


def test_weekly_periods_are_monday_to_sunday_clipped_to_the_range() -> None:
    weeks = periods(D(2026, 11, 4), D(2026, 11, 18), Granularity.WEEK, IST)  # Wed .. Wed
    assert [p.key for p in weeks] == [D(2026, 11, 2), D(2026, 11, 9), D(2026, 11, 16)]
    assert weeks[0].label == "2026-11-04 to 2026-11-08"
    assert weeks[-1].label == "2026-11-16 to 2026-11-18"


def test_monthly_periods_use_month_names_for_whole_months() -> None:
    months = periods(D(2026, 9, 1), D(2026, 11, 10), Granularity.MONTH, IST)
    assert [p.label for p in months] == ["Sep 2026", "Oct 2026", "2026-11-01 to 2026-11-10"]
    assert [p.key for p in months] == [D(2026, 9, 1), D(2026, 10, 1), D(2026, 11, 1)]


def test_total_is_one_period_for_the_whole_range() -> None:
    [whole] = periods(D(2026, 1, 15), D(2026, 3, 2), Granularity.TOTAL, IST)
    assert whole.key == D(2026, 1, 15)
    assert whole.end == _utc(2026, 3, 2, 18, 30)


def test_period_key_maps_days_to_their_period() -> None:
    first = D(2026, 11, 4)
    assert period_key(D(2026, 11, 12), Granularity.WEEK, first) == D(2026, 11, 9)
    assert period_key(D(2026, 11, 12), Granularity.MONTH, first) == D(2026, 11, 1)
    assert period_key(D(2026, 11, 12), Granularity.TOTAL, first) == first


def test_range_problems() -> None:
    today = D(2026, 11, 11)
    assert range_problem(D(2026, 11, 1), D(2026, 11, 11), Granularity.DAY, today) is None
    assert "before" in range_problem(D(2026, 11, 5), D(2026, 11, 1), Granularity.DAY, today)
    assert "future" in range_problem(D(2026, 12, 1), D(2026, 12, 2), Granularity.DAY, today)
    assert "maximum" in range_problem(D(2025, 1, 1), D(2026, 11, 1), Granularity.MONTH, today)
    assert "weekly or monthly" in range_problem(D(2026, 1, 1), D(2026, 11, 1),
                                                Granularity.DAY, today)


def _incident(start: dt.datetime, end: dt.datetime | None) -> Incident:
    return Incident(id=1, monitor_id=1, started_at=start, resolved_at=end)


def test_downtime_is_clipped_to_the_period() -> None:
    start, end = _utc(2026, 11, 1), _utc(2026, 11, 2)
    now = _utc(2026, 11, 10)
    spanning = _incident(_utc(2026, 10, 31, 23), _utc(2026, 11, 1, 1))  # 1 h inside
    inside = _incident(_utc(2026, 11, 1, 12), _utc(2026, 11, 1, 12, 30))  # 30 min
    assert downtime_seconds([spanning, inside], start, end, now) == 5400


def test_open_incident_counts_until_now() -> None:
    now = _utc(2026, 11, 1, 10)
    open_one = _incident(_utc(2026, 11, 1, 9), None)
    assert downtime_seconds([open_one], _utc(2026, 11, 1), _utc(2026, 11, 2), now) == 3600


def test_monitored_time_starts_at_creation_and_stops_at_now() -> None:
    start, end = _utc(2026, 11, 1), _utc(2026, 11, 2)
    assert monitored_seconds(_utc(2026, 11, 1, 12), start, end, _utc(2026, 11, 5)) == 12 * 3600
    assert monitored_seconds(None, start, end, _utc(2026, 11, 1, 6)) == 6 * 3600
    assert monitored_seconds(_utc(2026, 11, 3), start, end, _utc(2026, 11, 5)) == 0


def test_uptime_percent() -> None:
    assert uptime_percent(0, 86400) == 100.0
    assert uptime_percent(864, 86400) == 99.0
    assert uptime_percent(100, 0) is None
