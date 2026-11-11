"""CSV versions of the uptime report and the downtime log (opens in Excel)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from .reports import DowntimeEntry, PeriodFigures, UptimeReport

# Cells starting with these are run as formulas by Excel/Sheets; a monitor named
# "=HYPERLINK(...)" must stay text (CSV injection).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _write(rows: Iterable[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    for row in rows:
        writer.writerow([_safe(v) for v in row])
    # A byte-order mark makes Excel read the file as UTF-8 (customer names in Hindi etc.).
    return "﻿" + out.getvalue()


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _figures(f: PeriodFigures) -> list[object]:
    return [_pct(f.uptime_pct), f.downtime_seconds, round(f.downtime_seconds / 60, 1),
            f.incidents, f.checks, f.failed_checks,
            "" if f.avg_response_ms is None else f.avg_response_ms]


_FIGURE_HEADERS = ["Uptime %", "Downtime (s)", "Downtime (min)", "Outages",
                   "Checks", "Failed checks", "Avg response (ms)"]


def uptime_csv(report: UptimeReport, level: str) -> str:
    """One row per website (``level="monitor"``) or customer, per period."""
    def rows():
        if level == "customer":
            yield ["Customer", "Websites", "Period", *_FIGURE_HEADERS]
            for c in report.customers:
                for period, figures in zip(report.periods, c.periods):
                    yield [c.customer_name, c.monitors, period.label, *_figures(figures)]
        else:
            yield ["Customer", "Group", "Website", "URL", "Period", *_FIGURE_HEADERS]
            for m in report.monitors:
                for period, figures in zip(report.periods, m.periods):
                    yield [m.customer_name or "", m.group_name or "", m.name, m.url,
                           period.label, *_figures(figures)]
    return _write(rows())


def downtime_csv(entries: list[DowntimeEntry], tz: str) -> str:
    zone = ZoneInfo(tz)

    def local(moment: datetime | None) -> str:
        return "" if moment is None else moment.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")

    def rows():
        yield ["Customer", "Group", "Website", "URL", f"Started ({tz})", f"Ended ({tz})",
               "Downtime (s)", "Downtime (min)", "Reason", "How it ended"]
        for e in entries:
            yield [e.customer_name or "", e.group_name or "", e.monitor_name, e.url,
                   local(e.started_at), local(e.resolved_at) or "still down",
                   e.downtime_seconds, round(e.downtime_seconds / 60, 1), e.reason or "",
                   e.close_reason or ""]
    return _write(rows())
