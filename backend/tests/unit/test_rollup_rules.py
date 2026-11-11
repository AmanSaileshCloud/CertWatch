"""core.rollup: which hours to summarise and what may be deleted."""

from __future__ import annotations

import datetime as dt

from backend.ssl_monitor.core.rollup import chunks, deletion_cutoff, floor_hour, rollup_window

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 11, 11, 12, 34, 56, tzinfo=UTC)


def test_floor_hour_cuts_in_utc_even_for_ist_times() -> None:
    # 15:45 IST is 10:15 UTC: the hour starts at 10:00 UTC, not 15:00 IST (09:30 UTC).
    assert floor_hour(dt.datetime(2026, 11, 11, 15, 45, tzinfo=IST)) == dt.datetime(
        2026, 11, 11, 10, 0, tzinfo=UTC)


def test_first_run_starts_at_the_oldest_result_and_stops_before_the_current_hour() -> None:
    earliest = dt.datetime(2026, 11, 10, 7, 20, tzinfo=UTC)
    assert rollup_window(None, earliest, NOW) == (
        dt.datetime(2026, 11, 10, 7, tzinfo=UTC), dt.datetime(2026, 11, 11, 12, tzinfo=UTC))


def test_later_runs_redo_the_last_summarised_hour() -> None:
    last = dt.datetime(2026, 11, 11, 10, tzinfo=UTC)
    assert rollup_window(last, None, NOW) == (last, dt.datetime(2026, 11, 11, 12, tzinfo=UTC))


def test_nothing_to_do_without_data_or_inside_the_current_hour() -> None:
    assert rollup_window(None, None, NOW) is None
    assert rollup_window(None, dt.datetime(2026, 11, 11, 12, 1, tzinfo=UTC), NOW) is None


def test_chunks_cover_the_span_exactly_one_day_at_a_time() -> None:
    start = dt.datetime(2026, 11, 1, 5, tzinfo=UTC)
    end = dt.datetime(2026, 11, 3, 8, tzinfo=UTC)
    spans = chunks(start, end)
    assert spans[0][0] == start and spans[-1][1] == end
    assert len(spans) == 3
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


def test_deletion_waits_for_the_summary() -> None:
    assert deletion_cutoff(NOW, 30, None) is None


def test_deletion_cutoff_is_the_retention_age() -> None:
    assert deletion_cutoff(NOW, 30, NOW) == NOW - dt.timedelta(days=30)


def test_deletion_never_passes_the_summarised_hours() -> None:
    summarised = NOW - dt.timedelta(days=40)
    assert deletion_cutoff(NOW, 30, summarised) == summarised
