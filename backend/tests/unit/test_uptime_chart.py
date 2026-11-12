"""Unit tests for the alert email's response-time chart (PNG via Pillow)."""

from __future__ import annotations

import datetime as dt
import io

from PIL import Image

from backend.ssl_monitor.adapters.notifier import uptime_chart
from backend.ssl_monitor.adapters.notifier.uptime_chart import (
    OUTPUT_SCALE,
    chart_alt_text,
    render_response_chart,
)
from backend.ssl_monitor.core.uptime_models import CheckResult

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
START = NOW - dt.timedelta(hours=24)


def _check(minutes_ago: int, ms: int | None = 800, up: bool = True) -> CheckResult:
    return CheckResult(monitor_id=1, checked_at=NOW - dt.timedelta(minutes=minutes_ago),
                       is_up=up, response_ms=ms)


def _render(results):
    return render_response_chart(results, start=START, end=NOW, interval_seconds=300)


def test_renders_a_png_of_the_email_size():
    png = _render([_check(m) for m in range(0, 600, 5)] + [_check(700, None, up=False)])
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.size == (uptime_chart.WIDTH * OUTPUT_SCALE, uptime_chart.HEIGHT * OUTPUT_SCALE)


def test_no_checks_no_chart():
    assert _render([]) is None


def test_naive_times_and_other_timezones_work():
    naive = CheckResult(monitor_id=1, checked_at=NOW.replace(tzinfo=None), is_up=True,
                        response_ms=500)
    assert render_response_chart([naive], start=START, end=NOW, interval_seconds=60,
                                  tz="Asia/Kolkata")


def test_drawing_failure_returns_none(monkeypatch, caplog):
    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(uptime_chart._Chart, "png", broken)
    assert _render([_check(5)]) is None
    assert "Could not draw" in caplog.text


def test_line_breaks_at_down_checks_and_gaps():
    points = [_check(m) for m in (100, 95, 90)] + [_check(85, None, up=False)] + \
             [_check(m) for m in (80, 75)] + [_check(10)]  # 65-minute gap before the last
    times = [p.checked_at.timestamp() for p in points]
    max_gap = uptime_chart._gap_threshold(times, 300)
    segments = uptime_chart._segments(points, times, max_gap)
    assert [len(s) for s in segments] == [3, 2, 1]
    spans = uptime_chart._down_spans(points, times, max_gap)
    assert spans == [(times[3], times[4])]  # the down check runs until the next check


def test_axis_labels_are_friendly():
    assert uptime_chart._nice_ceil(6800) == 10000
    assert uptime_chart._nice_ceil(130) == 200
    assert uptime_chart._format_ms(2500) == "2.50 s"
    assert uptime_chart._format_ms(250) == "250 ms"


def test_alt_text_summarises_the_checks():
    text = chart_alt_text([_check(5, 500), _check(10, 1500), _check(15, None, up=False)], 24)
    assert text == ("Response time, last 24 hours: 3 checks, average 1000 ms, fastest 500 ms, "
                    "slowest 1500 ms, 1 down checks.")
