"""Response-time chart for the uptime alert email, drawn as a PNG with Pillow.

Email apps (Gmail, Outlook) drop SVG and scripts, so the dashboard's chart
(frontend ``ResponseTimeChart.tsx``) is redrawn here as an image with the same
rules: a line for successful checks, broken at down checks and at gaps in
checking, and red bands where the site was down.

Best effort: ``render_response_chart`` returns None instead of raising, so a
drawing problem never stops the alert from going out.
"""

from __future__ import annotations

import io
import logging
import math
from collections.abc import Sequence
from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from ...core.uptime_models import CheckResult

logger = logging.getLogger("ssl_monitor.notifier")

# Size shown in the email (CSS pixels). The PNG is drawn at OUTPUT_SCALE × this so
# it stays sharp on high-DPI screens, and first at DRAW_SCALE × this, then shrunk,
# which smooths the line's edges (Pillow does not anti-alias lines).
WIDTH, HEIGHT = 800, 240
OUTPUT_SCALE = 2
DRAW_SCALE = 4

MARGIN_TOP, MARGIN_RIGHT, MARGIN_BOTTOM, MARGIN_LEFT = 14, 14, 54, 56
DOWN_STRIP = 6
Y_TICKS = 4
X_TICKS = 6
GAP_FACTOR = 2.5

# Light-theme dashboard colours.
BACKGROUND = (255, 255, 255)
GRID = (238, 241, 246)
AXIS = (203, 213, 225)
LABEL = (107, 114, 128)
LINE = (91, 77, 232)
DOWN = (220, 38, 38)
DOWN_BAND = (252, 233, 233)  # DOWN at ~9% over white


def render_response_chart(
    results: Sequence[CheckResult],
    *,
    start: datetime,
    end: datetime,
    interval_seconds: int,
    tz: str = "UTC",
) -> bytes | None:
    """PNG bytes of the response-time chart for ``results`` between ``start`` and
    ``end`` (axis times shown in time zone ``tz``); None if there are no checks or
    drawing failed."""
    if not results:
        return None
    try:
        return _Chart(results, start, end, interval_seconds, ZoneInfo(tz)).png()
    except Exception:  # noqa: BLE001 - the alert still goes out without the chart
        logger.exception("Could not draw the response-time chart")
        return None


def chart_alt_text(results: Sequence[CheckResult], hours: int) -> str:
    """A one-line summary of the chart, for the image's alt text."""
    times = [r.response_ms for r in results if r.is_up and r.response_ms is not None]
    down = sum(1 for r in results if not r.is_up)
    if not times:
        return f"Response time, last {hours} hours: no successful checks; {down} down checks."
    return (
        f"Response time, last {hours} hours: {len(results)} checks, average "
        f"{round(sum(times) / len(times))} ms, fastest {min(times)} ms, slowest "
        f"{max(times)} ms, {down} down checks."
    )


class _Chart:
    def __init__(
        self,
        results: Sequence[CheckResult],
        start: datetime,
        end: datetime,
        interval_seconds: int,
        tz: ZoneInfo,
    ) -> None:
        self.points = sorted(results, key=lambda r: _utc(r.checked_at))
        self.times = [_utc(r.checked_at).timestamp() for r in self.points]
        self.start = _utc(start).timestamp()
        self.end = max(_utc(end).timestamp(), self.start + 1)
        self.tz = tz
        self.max_gap = _gap_threshold(self.times, interval_seconds)
        biggest = max((r.response_ms or 0 for r in self.points), default=0)
        self.y_max = _nice_ceil(max(100.0, biggest * 1.1))

        s = DRAW_SCALE
        self.s = s
        self.image = Image.new("RGB", (WIDTH * s, HEIGHT * s), BACKGROUND)
        self.draw = ImageDraw.Draw(self.image)
        self.font = _font(11 * s)
        self.left = MARGIN_LEFT * s
        self.right = (WIDTH - MARGIN_RIGHT) * s
        self.top = MARGIN_TOP * s
        self.baseline = (HEIGHT - MARGIN_BOTTOM) * s

    # ── coordinates (in drawing pixels) ──

    def x(self, t: float) -> float:
        return self.left + (t - self.start) / (self.end - self.start) * (self.right - self.left)

    def y(self, ms: float) -> float:
        usable = self.baseline - self.top - DOWN_STRIP * self.s
        return self.baseline - DOWN_STRIP * self.s - ms / self.y_max * usable

    # ── drawing ──

    def png(self) -> bytes:
        self._grid_and_labels()
        self._down_spans()
        self.draw.line([(self.left, self.baseline), (self.right, self.baseline)],
                       fill=AXIS, width=self.s)
        self._line()
        self._legend()
        size = (WIDTH * OUTPUT_SCALE, HEIGHT * OUTPUT_SCALE)
        out = io.BytesIO()
        self.image.resize(size, Image.Resampling.LANCZOS).save(out, format="PNG", optimize=True)
        return out.getvalue()

    def _grid_and_labels(self) -> None:
        for i in range(Y_TICKS + 1):
            value = self.y_max / Y_TICKS * i
            y = self.y(value)
            self.draw.line([(self.left, y), (self.right, y)], fill=GRID, width=self.s)
            self._text(_format_ms(value), self.left - 8 * self.s, y, align="right", middle=True)
        label_y = self.baseline + 8 * self.s
        for i in range(X_TICKS + 1):
            t = self.start + (self.end - self.start) / X_TICKS * i
            align = "left" if i == 0 else "right" if i == X_TICKS else "center"
            self._text(self._clock(t), self.x(t), label_y, align=align)

    def _down_spans(self) -> None:
        for began, ended in _down_spans(self.points, self.times, self.max_gap):
            x0 = self.x(began)
            x1 = max(self.x(ended), x0 + 3 * self.s)
            self.draw.rectangle([x0, self.top, x1, self.baseline], fill=DOWN_BAND)
            self.draw.rectangle([x0, self.baseline - DOWN_STRIP * self.s, x1, self.baseline],
                                fill=DOWN)

    def _line(self) -> None:
        width = 2 * self.s
        for segment in _segments(self.points, self.times, self.max_gap):
            coords = [(self.x(t), self.y(ms)) for t, ms in segment]
            if len(coords) > 1:
                self.draw.line(coords, fill=LINE, width=width, joint="curve")
            radius = width / 2  # round the ends; a lone check shows as a dot
            for cx, cy in (coords[0], coords[-1]):
                self.draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=LINE)

    def _legend(self) -> None:
        s = self.s
        y = (HEIGHT - 12) * s
        x = self.left
        self.draw.line([(x, y), (x + 16 * s, y)], fill=LINE, width=2 * s)
        x = self._text("Response time", x + 22 * s, y, middle=True) + 18 * s
        self.draw.rectangle([x, y - 4 * s, x + 12 * s, y + 4 * s], fill=DOWN)
        self._text("Down", x + 18 * s, y, middle=True)

    def _text(self, text: str, x: float, y: float, *, align: str = "left",
              middle: bool = False) -> float:
        """Draw ``text`` anchored at x (left/center/right edge) and y (top, or middle);
        returns the x where the text ends."""
        left, top, right, bottom = self.draw.textbbox((0, 0), text, font=self.font)
        width, height = right - left, bottom - top
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        if middle:
            y -= height / 2
        self.draw.text((x - left, y - top), text, font=self.font, fill=LABEL)
        return x + width

    def _clock(self, t: float) -> str:
        local = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(self.tz)
        return local.strftime("%I:%M %p")


# ── helpers (same rules as the dashboard chart) ─────────────────


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _nice_ceil(value: float) -> float:
    """Round up to 1/2/5 × 10ⁿ so axis labels are friendly."""
    exp = 10 ** math.floor(math.log10(value))
    step = next((m for m in (1, 2, 5, 10) if m * exp >= value), 10)
    return step * exp


def _format_ms(ms: float) -> str:
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{round(ms)} ms"


def _gap_threshold(times: list[float], interval_seconds: int) -> float:
    """Checks further apart than GAP_FACTOR × the larger of the check interval and the
    typical (median) spacing count as a gap in checking."""
    spacings = [b - a for a, b in zip(times, times[1:])]
    typical = median(spacings) if spacings else 0.0
    return GAP_FACTOR * max(float(interval_seconds), typical)


def _down_spans(
    points: list[CheckResult], times: list[float], max_gap: float
) -> list[tuple[float, float]]:
    """Runs of down checks → (start, end) times; a run ends at the next check, or at
    its own last check when a gap follows."""
    spans: list[tuple[float, float]] = []
    began: float | None = None
    for i, point in enumerate(points):
        if not point.is_up and began is None:
            began = times[i]
        has_next = i + 1 < len(points)
        gap_after = has_next and times[i + 1] - times[i] > max_gap
        if began is not None and (not has_next or points[i + 1].is_up or gap_after):
            spans.append((began, times[i + 1] if has_next and not gap_after else times[i]))
            began = None
    return spans


def _segments(
    points: list[CheckResult], times: list[float], max_gap: float
) -> list[list[tuple[float, int]]]:
    """Successful checks as (time, ms) runs, broken at down checks and gaps."""
    segments: list[list[tuple[float, int]]] = []
    current: list[tuple[float, int]] = []
    for i, point in enumerate(points):
        if not point.is_up or point.response_ms is None:
            current = []
            continue
        if current and times[i] - times[i - 1] > max_gap:
            current = []
        if not current:
            segments.append(current)
        current.append((times[i], point.response_ms))
    return segments


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except (OSError, ValueError, TypeError):  # Pillow built without FreeType
        return ImageFont.load_default()
