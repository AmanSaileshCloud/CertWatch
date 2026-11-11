"""Uptime report rules. Pure: no I/O, no clock reads.

**Uptime %** is time-based, like an SLA: for each period,

    uptime % = 100 × (1 − downtime / monitored time)

* *downtime* is the part of each incident (confirmed outage) inside the period.
  An incident starts at the first failed check of the run that confirmed it and
  ends when the site recovered (still open → counts up to "now").
* *monitored time* is the part of the period after the monitor was created and
  not in the future. A period with no monitored time has no uptime (None).

Report periods are calendar days, weeks (Monday–Sunday) or months in the
report's time zone (IST by default), clipped to the requested date range.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from .uptime_models import Incident

MAX_RANGE_DAYS = 366
MAX_DAILY_RANGE_DAYS = 92


class Granularity(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    TOTAL = "total"  # one period covering the whole range


@dataclass(frozen=True)
class CheckStats:
    """Check counts and response times for one monitor in one period."""

    checks: int = 0
    up_checks: int = 0
    response_ms_sum: float = 0.0  # sum of response times (ms) over response_ms_count checks
    response_ms_count: int = 0

    def __add__(self, other: "CheckStats") -> "CheckStats":
        return CheckStats(self.checks + other.checks, self.up_checks + other.up_checks,
                          self.response_ms_sum + other.response_ms_sum,
                          self.response_ms_count + other.response_ms_count)

    @property
    def avg_response_ms(self) -> int | None:
        if not self.response_ms_count:
            return None
        return round(self.response_ms_sum / self.response_ms_count)


# How the database groups check stats before they are folded into report periods
# (a whole-range report is folded from monthly groups).
STATS_UNIT = {"day": "day", "week": "week", "month": "month", "total": "month"}


@dataclass(frozen=True)
class Period:
    key: date  # calendar start of the period (also how stats rows are matched)
    label: str
    start: datetime  # UTC, inclusive
    end: datetime  # UTC, exclusive


def range_problem(first: date, last: date, granularity: Granularity, today: date) -> str | None:
    """Why this date range can't be reported on, or None if it's fine."""
    if last < first:
        return "The end date is before the start date."
    if first > today:
        return "The start date is in the future."
    days = (last - first).days + 1
    if days > MAX_RANGE_DAYS:
        return f"The range is {days} days; the maximum is {MAX_RANGE_DAYS}."
    if granularity is Granularity.DAY and days > MAX_DAILY_RANGE_DAYS:
        return (f"Daily breakdowns cover at most {MAX_DAILY_RANGE_DAYS} days; "
                "choose weekly or monthly for a longer range.")
    return None


def period_key(day: date, granularity: Granularity, first: date) -> date:
    """The period a calendar day belongs to (by its calendar start)."""
    if granularity is Granularity.DAY:
        return day
    if granularity is Granularity.WEEK:
        return day - timedelta(days=day.weekday())
    if granularity is Granularity.MONTH:
        return day.replace(day=1)
    return first


def periods(first: date, last: date, granularity: Granularity, tz: ZoneInfo) -> list[Period]:
    """The report periods from ``first`` to ``last`` (both inclusive, local dates)."""
    result: list[Period] = []
    day = first
    while day <= last:
        key = period_key(day, granularity, first)
        stop = min(_next_calendar_start(key, granularity, last), last + timedelta(days=1))
        result.append(Period(key=key, label=_label(day, stop - timedelta(days=1), granularity),
                             start=_utc_midnight(day, tz), end=_utc_midnight(stop, tz)))
        day = stop
    return result


def _next_calendar_start(key: date, granularity: Granularity, last: date) -> date:
    if granularity is Granularity.DAY:
        return key + timedelta(days=1)
    if granularity is Granularity.WEEK:
        return key + timedelta(days=7)
    if granularity is Granularity.MONTH:
        return (key.replace(day=28) + timedelta(days=4)).replace(day=1)
    return last + timedelta(days=1)


def _label(start: date, end: date, granularity: Granularity) -> str:
    if granularity is Granularity.DAY:
        return start.isoformat()
    if granularity is Granularity.MONTH and start.day == 1 and \
            (end + timedelta(days=1)).day == 1:
        return start.strftime("%b %Y")
    return f"{start.isoformat()} to {end.isoformat()}"


def _utc_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def _overlap_seconds(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    return max(0.0, (min(a_end, b_end) - max(a_start, b_start)).total_seconds())


def downtime_seconds(incidents: Iterable[Incident], start: datetime, end: datetime,
                     now: datetime) -> float:
    """Seconds of confirmed outage inside [start, end). A monitor has at most one
    open incident at a time, so its incidents never overlap one another."""
    return sum(_overlap_seconds(i.started_at, i.resolved_at or now, start, end)
               for i in incidents)


def monitored_seconds(created_at: datetime | None, start: datetime, end: datetime,
                      now: datetime) -> float:
    """Seconds of [start, end) that the monitor existed for, up to ``now``."""
    return _overlap_seconds(created_at or start, now, start, end)


def uptime_percent(downtime: float, monitored: float) -> float | None:
    if monitored <= 0:
        return None
    return round(max(0.0, 100.0 * (1.0 - downtime / monitored)), 3)
