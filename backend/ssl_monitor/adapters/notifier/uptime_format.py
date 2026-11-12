"""Uptime alert email formatter (pure — no I/O).

Turns one alert (DOWN / STILL DOWN reminder / RECOVERED / TEST) about one
incident into an ``EmailMessage``: a subject, a plain-text body and an HTML
email laid out as a status-page style report — a full-width coloured banner
("Shop - Monitor is Down / Down since …"), a stacked list of details, then an
Availability Summary (last 3 days) and a Performance Report (last 24 hours, with
a response-time chart attached as an inline PNG) when an ``AlertReport`` is given.

Monitor names, URLs and error reasons come from users and remote sites, so every
such value is HTML-escaped before it goes into the HTML part, and the names in the
subject are flattened to one line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from ...core.alert_models import (
    AlertCandidate,
    AlertKind,
    AlertReport,
    EmailMessage,
    InlineImage,
)
from ...core.uptime_models import Incident
from .uptime_chart import WIDTH as CHART_WIDTH
from .uptime_chart import chart_alt_text, render_response_chart

_EMPTY = "—"
CHART_CID = "response-chart"


@dataclass(frozen=True)
class _Style:
    banner: str  # banner background colour
    title: str  # "<name> - <title>"
    since: str  # "<since> <time>" under the title
    status: str


_STYLES = {
    AlertKind.DOWN: _Style("#e5484d", "Monitor is Down", "Down since", "DOWN"),
    AlertKind.REMINDER: _Style("#e5484d", "Monitor is Still Down", "Down since", "DOWN"),
    AlertKind.RECOVERED: _Style("#2ecc71", "Monitor is Available", "Available since", "UP"),
    AlertKind.TEST: _Style("#4733e0", "Test Alert", "Sent at", "TEST"),
}


@dataclass(frozen=True)
class _Row:
    label: str
    value: str  # NOT escaped
    link: bool = False  # render the value as a link (only http/https URLs are linked)


def format_duration(seconds: float) -> str:
    """Compact human duration: "45s", "9m 40s", "1h 30m", "2d 3h" (zero tails dropped)."""
    total = max(0, int(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return _two_units(days, "d", hours, "h")
    if hours:
        return _two_units(hours, "h", minutes, "m")
    if minutes:
        return _two_units(minutes, "m", secs, "s")
    return f"{secs}s"


def format_duration_long(seconds: float) -> str:
    """Spelled-out duration for the email body: "0 Mins 41 Secs", "1 Hrs 30 Mins",
    "2 Days 3 Hrs"."""
    total = max(0, int(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} Days {hours} Hrs"
    if hours:
        return f"{hours} Hrs {minutes} Mins"
    return f"{minutes} Mins {secs} Secs"


def format_alert_time(value: datetime | None, tz: str = "UTC") -> str:
    """"12-Nov-2026, 11:36:55 am IST" — ``value`` in time zone ``tz``; naive = UTC."""
    if value is None:
        return _EMPTY
    local = _as_utc(value).astimezone(ZoneInfo(tz))
    am_pm = local.strftime("%p").lower()
    return local.strftime(f"%d-%b-%Y, %I:%M:%S {am_pm} %Z")


def _two_units(big: int, big_unit: str, small: int, small_unit: str) -> str:
    return f"{big}{big_unit} {small}{small_unit}" if small else f"{big}{big_unit}"


def format_uptime_alert(
    kind: AlertKind,
    candidate: AlertCandidate,
    *,
    now: datetime,
    to: Sequence[str],
    public_url: str | None,
    tz: str = "UTC",
    report: AlertReport | None = None,
) -> EmailMessage:
    """``tz`` is the time zone the email's timestamps are shown in (REPORT_TIMEZONE);
    ``report`` adds the IP and the availability/performance summaries."""
    seconds = _incident_seconds(candidate.incident, now)
    rows = _detail_rows(kind, candidate, seconds, report)
    link = _dashboard_link(public_url, candidate.monitor.id)
    since = _since(kind, candidate.incident, now, tz)
    chart = _chart_image(candidate, report, now, tz)
    return EmailMessage(
        subject=f"{_subject_prefix(kind, format_duration(seconds))} {_title(candidate)}",
        text=_text_body(kind, candidate, since, rows, report, link),
        html=_html_body(kind, candidate, since, rows, report, link, chart is not None),
        to=tuple(to),
        images=(chart,) if chart else (),
    )


def _chart_image(
    candidate: AlertCandidate, report: AlertReport | None, now: datetime, tz: str
) -> InlineImage | None:
    """The response-time chart for the Performance Report; None without data."""
    if report is None or not report.response_series:
        return None
    png = render_response_chart(
        report.response_series,
        start=now - timedelta(hours=report.performance_hours),
        end=now,
        interval_seconds=candidate.monitor.interval_seconds,
        tz=tz,
    )
    return InlineImage(CHART_CID, png) if png else None


def _title(candidate: AlertCandidate) -> str:
    """Customer · Group · Monitor, on one line (it ends the email subject)."""
    parts = (candidate.customer_name, candidate.group_name, candidate.monitor.name)
    cleaned = (_one_line(part) for part in parts if part)
    return " · ".join(part for part in cleaned if part)


def _one_line(text: str) -> str:
    """Whitespace runs collapsed to one space, control characters dropped — a name
    can never break the subject header onto a second line."""
    collapsed = " ".join(text.split())
    return "".join(char for char in collapsed if ord(char) >= 32 and ord(char) != 127)


def _subject_prefix(kind: AlertKind, duration: str) -> str:
    if kind is AlertKind.REMINDER:
        return f"[STILL DOWN {duration}]"
    if kind is AlertKind.RECOVERED:
        return f"[RECOVERED after {duration}]"
    return f"[{kind.value.upper()}]"


def _incident_seconds(incident: Incident, now: datetime) -> float:
    if incident.duration_seconds is not None:
        return incident.duration_seconds
    end = incident.resolved_at or now
    return (_as_utc(end) - _as_utc(incident.started_at)).total_seconds()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _since(kind: AlertKind, incident: Incident, now: datetime, tz: str) -> str:
    """The banner's second line: "Down since …" / "Available since …" / "Sent at …"."""
    when = {
        AlertKind.RECOVERED: incident.resolved_at,
        AlertKind.TEST: now,
    }.get(kind, incident.started_at)
    return f"{_STYLES[kind].since} {format_alert_time(when, tz)}"


def _detail_rows(
    kind: AlertKind, candidate: AlertCandidate, seconds: float, report: AlertReport | None
) -> list[_Row]:
    """The stacked label/value list under the banner (shared by text and HTML)."""
    monitor, incident = candidate.monitor, candidate.incident
    rows = [_Row("Site monitored", monitor.url, link=True)]
    group = " · ".join(_one_line(p) for p in (candidate.customer_name, candidate.group_name) if p)
    if group:
        rows.append(_Row("Customer / Group", group))
    if report and report.resolved_ip:
        rows.append(_Row("Resolved IP", report.resolved_ip))
    reason = incident.reason or monitor.last_error or _EMPTY
    if kind is AlertKind.RECOVERED:
        rows += [
            _Row("Last Down Duration", format_duration_long(seconds)),
            _Row("Reason(s)", f"{reason} (Resolved)" if reason != _EMPTY else _EMPTY),
        ]
    else:
        rows += [
            _Row("Down Duration", format_duration_long(seconds)),
            _Row("Reason(s)", reason),
        ]
    if kind is AlertKind.REMINDER:
        rows.append(_Row("Reminder", f"#{incident.reminder_count + 1}"))
    rows += [
        _Row("Last Status Code", str(monitor.last_status_code or _EMPTY)),
        _Row("Check Settings", _check_settings(candidate)),
    ]
    return rows


def _check_settings(candidate: AlertCandidate) -> str:
    """"Every 5m · expects HTTP 200 · timeout 10s" (+ the keyword, when one is set)."""
    monitor = candidate.monitor
    parts = [
        f"Every {format_duration(monitor.interval_seconds)}",
        f"expects HTTP {monitor.expected_status}",
        f"timeout {monitor.timeout_seconds}s",
    ]
    if monitor.keyword:
        parts.append(f'keyword "{_one_line(monitor.keyword)}"')
    return " · ".join(parts)


def _dashboard_link(public_url: str | None, monitor_id: int) -> str | None:
    return f"{public_url.rstrip('/')}/#/websites/{monitor_id}" if public_url else None


# ── Report figures ───────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return f"{value:.3f} %" if value is not None else _EMPTY


def _ms(value: int | None) -> str:
    return f"{value} ms" if value is not None else _EMPTY


def _summary_tiles(report: AlertReport) -> list[tuple[str, str]]:
    return [
        ("Availability", _pct(report.availability_pct)),
        ("No. of Downtimes", str(report.downtimes)),
        ("Downtime Duration", format_duration_long(report.downtime_seconds)),
    ]


def _performance_tiles(report: AlertReport) -> list[tuple[str, str]]:
    return [
        ("Uptime", _pct(report.uptime_pct)),
        ("Avg. Response Time", _ms(report.avg_response_ms)),
        ("Fastest", _ms(report.min_response_ms)),
        ("Slowest", _ms(report.max_response_ms)),
        ("Checks", str(report.checks)),
    ]


def _summary_heading(report: AlertReport) -> tuple[str, str]:
    return "Availability Summary Report", f"Last {report.summary_days} days"


def _performance_heading(report: AlertReport) -> tuple[str, str]:
    return "Performance Report", f"Last {report.performance_hours} Hours"


# ── Plain text ───────────────────────────────────────────────────


def _text_body(
    kind: AlertKind,
    candidate: AlertCandidate,
    since: str,
    rows: list[_Row],
    report: AlertReport | None,
    link: str | None,
) -> str:
    lines = [f"{_one_line(candidate.monitor.name)} - {_STYLES[kind].title}", since, ""]
    lines += [f"{row.label}: {row.value}" for row in rows]
    if link:
        lines += ["", f"View in CertWatch: {link}"]
    if report:
        for (title, span), tiles in (
            (_summary_heading(report), _summary_tiles(report)),
            (_performance_heading(report), _performance_tiles(report)),
        ):
            lines += ["", f"{title} - {span}"]
            lines += [f"  {label}: {value}" for label, value in tiles]
    return "\n".join(lines) + "\n"


# ── HTML (inline styles only: email clients drop <style> blocks) ─


_FONT = "font-family:Arial,Helvetica,sans-serif;"


def _html_value(row: _Row) -> str:
    if row.link and row.value.lower().startswith(("http://", "https://")):
        return (
            f'<a href="{escape(row.value)}" style="color:#2b6cb0;font-weight:700;'
            f'text-decoration:none;word-break:break-all;">{escape(row.value)}</a>'
        )
    return escape(row.value)


def _html_row(row: _Row) -> str:
    return (
        '<tr><td style="padding:0 0 12px;">'
        f'<div style="{_FONT}color:#6b7280;font-size:14px;">{escape(row.label)}</div>'
        f'<div style="{_FONT}color:#111827;font-size:13px;font-weight:700;margin-top:2px;'
        f'word-break:break-word;">{_html_value(row)}</div>'
        "</td></tr>"
    )


def _html_link(link: str | None) -> str:
    if not link:
        return ""
    return (
        f'<p style="{_FONT}margin:12px 0 0;font-size:12px;">'
        f'<a href="{escape(link)}" style="color:#2b6cb0;font-weight:700;text-decoration:none;">'
        "View in CertWatch</a></p>"
    )


def _html_section(heading: tuple[str, str], tiles: list[tuple[str, str]]) -> str:
    title, span = heading
    cells = "".join(
        f'<td align="center" style="{_FONT}padding:6px 4px;vertical-align:top;">'
        f'<div style="color:#6b7280;font-size:12px;">{escape(label)}</div>'
        f'<div style="color:#111827;font-size:14px;font-weight:700;margin-top:3px;">'
        f"{escape(value)}</div></td>"
        for label, value in tiles
    )
    return (
        f'<div style="{_FONT}color:#111827;font-size:19px;margin:34px 0 16px;">'
        f'{escape(title)} - <span style="font-size:12px;">{escape(span)}</span></div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;"><tr>{cells}</tr></table>'
    )


def _html_chart(report: AlertReport) -> str:
    alt = escape(chart_alt_text(report.response_series, report.performance_hours))
    return (
        f'<div style="margin:18px 0 0;padding:12px;border:1px solid #e5e7eb;border-radius:10px;">'
        f'<img src="cid:{CHART_CID}" width="{CHART_WIDTH}" alt="{alt}" '
        f'style="display:block;width:100%;max-width:{CHART_WIDTH}px;height:auto;border:0;">'
        "</div>"
    )


def _html_report(report: AlertReport | None, with_chart: bool) -> str:
    if report is None:
        return ""
    html = (_html_section(_summary_heading(report), _summary_tiles(report))
            + _html_section(_performance_heading(report), _performance_tiles(report)))
    return html + _html_chart(report) if with_chart else html


def _html_body(
    kind: AlertKind,
    candidate: AlertCandidate,
    since: str,
    rows: list[_Row],
    report: AlertReport | None,
    link: str | None,
    with_chart: bool = False,
) -> str:
    style = _STYLES[kind]
    table = "".join(_html_row(row) for row in rows)
    return f"""\
<div style="{_FONT}max-width:840px;margin:0 auto;background:#ffffff;">
  <div style="background:{style.banner};padding:10px 16px 12px;text-align:center;color:#ffffff;">
    <div style="{_FONT}font-size:24px;line-height:1.3;">{escape(_one_line(candidate.monitor.name))} - {escape(style.title)}</div>
    <div style="{_FONT}font-size:11px;font-weight:700;margin-top:2px;">{escape(since)}</div>
  </div>

  <div style="padding:24px 0 8px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{table}</table>
    {_html_link(link)}
    {_html_report(report, with_chart)}
  </div>

  <div style="{_FONT}padding:18px 0 6px;margin-top:26px;border-top:1px solid #eef2f7;color:#94a3b8;font-size:11px;text-align:center;">
    CERTWATCH &middot; website uptime monitoring &middot; by Workmates
  </div>
</div>"""
