"""Uptime alert email formatter (pure — no I/O).

Turns one alert (DOWN / STILL DOWN reminder / RECOVERED / TEST) about one
incident into an ``EmailMessage``: a subject, a plain-text body and an
inline-styled HTML card in the same brand style as the SSL alert card.

Monitor names, URLs and error reasons come from users and remote sites, so every
such value is HTML-escaped before it goes into the HTML part, and the names in the
subject are flattened to one line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape

from ...core.alert_models import AlertCandidate, AlertKind, EmailMessage
from ...core.uptime_models import Incident

_EMPTY = "—"


@dataclass(frozen=True)
class _Style:
    text: str
    bg: str
    border: str
    headline: str
    status: str


_STYLES = {
    AlertKind.DOWN: _Style("#991b1b", "#fef2f2", "#fecaca", "WEBSITE DOWN", "DOWN"),
    AlertKind.REMINDER: _Style("#991b1b", "#fef2f2", "#fecaca", "WEBSITE STILL DOWN", "DOWN"),
    AlertKind.RECOVERED: _Style("#166534", "#f0fdf4", "#bbf7d0", "WEBSITE RECOVERED", "UP"),
    AlertKind.TEST: _Style("#3730a3", "#eef2ff", "#c7d2fe", "TEST ALERT", "TEST"),
}


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


def _two_units(big: int, big_unit: str, small: int, small_unit: str) -> str:
    return f"{big}{big_unit} {small}{small_unit}" if small else f"{big}{big_unit}"


def format_uptime_alert(
    kind: AlertKind,
    candidate: AlertCandidate,
    *,
    now: datetime,
    to: Sequence[str],
    public_url: str | None,
) -> EmailMessage:
    duration = format_duration(_incident_seconds(candidate.incident, now))
    rows = _detail_rows(kind, candidate, duration)
    link = _dashboard_link(public_url, candidate.monitor.id)
    return EmailMessage(
        subject=f"{_subject_prefix(kind, duration)} {_title(candidate)}",
        text=_text_body(kind, candidate, rows, link),
        html=_html_body(kind, candidate, rows, link, now),
        to=tuple(to),
    )


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


def _format_time(value: datetime | None) -> str:
    return _as_utc(value).strftime("%d %b %Y, %H:%M UTC") if value else _EMPTY


def _detail_rows(kind: AlertKind, candidate: AlertCandidate, duration: str) -> list[tuple[str, str]]:
    """Label/value pairs shared by the text and HTML bodies (values NOT escaped)."""
    monitor, incident = candidate.monitor, candidate.incident
    rows = [
        ("Website", monitor.name),
        ("URL", monitor.url),
        ("Status", _STYLES[kind].status),
        ("Down since", _format_time(incident.started_at)),
    ]
    if kind is AlertKind.RECOVERED:
        rows.append(("Recovered at", _format_time(incident.resolved_at)))
    rows += [
        ("Duration", duration),
        ("Reason", incident.reason or monitor.last_error or _EMPTY),
        ("Last status code", str(monitor.last_status_code or _EMPTY)),
        ("Check interval", f"every {format_duration(monitor.interval_seconds)}"),
    ]
    if kind is AlertKind.REMINDER:
        rows.append(("Reminder", f"#{incident.reminder_count + 1}"))
    return rows


def _dashboard_link(public_url: str | None, monitor_id: int) -> str | None:
    return f"{public_url.rstrip('/')}/#/websites/{monitor_id}" if public_url else None


def _text_body(
    kind: AlertKind, candidate: AlertCandidate, rows: list[tuple[str, str]], link: str | None
) -> str:
    lines = [f"{_STYLES[kind].headline}: {_title(candidate)}", ""]
    lines += [f"{label}: {value}" for label, value in rows]
    if link:
        lines += ["", f"View in CertWatch: {link}"]
    return "\n".join(lines) + "\n"


def _html_row(label: str, value: str) -> str:
    return (
        "<tr>"
        f'<td style="padding:7px 0;color:#64748b;font-size:13px;width:150px;vertical-align:top;">{escape(label)}</td>'
        f'<td style="padding:7px 0;color:#1e293b;font-size:13px;font-weight:600;word-break:break-all;">{escape(value)}</td>'
        "</tr>"
    )


def _html_button(link: str | None) -> str:
    if not link:
        return ""
    return (
        f'<p style="margin:18px 2px 0;"><a href="{escape(link)}" style="display:inline-block;'
        "background:#4733e0;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;"
        'padding:9px 16px;border-radius:8px;">View in CertWatch</a></p>'
    )


def _html_url_chip(url: str) -> str:
    style = (
        "display:inline-block;background:#ffffff;color:#111827;text-decoration:none;font-size:15px;"
        "font-weight:700;padding:5px 12px;border-radius:8px;margin:8px 0 12px;"
        "font-family:ui-monospace,Menlo,monospace;word-break:break-all;"
    )
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{escape(url)}" style="{style}">{escape(url)}</a>'
    return f'<span style="{style}">{escape(url)}</span>'


def _html_body(
    kind: AlertKind,
    candidate: AlertCandidate,
    rows: list[tuple[str, str]],
    link: str | None,
    now: datetime,
) -> str:
    style = _STYLES[kind]
    table = "".join(_html_row(label, value) for label, value in rows)
    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:linear-gradient(135deg,#6c5cff 0%,#4733e0 55%,#2a1e7a 100%);padding:22px 26px;color:#fff;">
    <div style="font-size:11px;letter-spacing:2px;opacity:.85;text-transform:uppercase;">CERTWATCH · Uptime Alert</div>
    <div style="font-size:18px;font-weight:700;margin-top:8px;">{escape(_title(candidate))}</div>
    {_html_url_chip(candidate.monitor.url)}
    <div style="font-size:11px;opacity:.8;">Generated {_format_time(now)}</div>
  </div>

  <div style="padding:22px 26px;">
    <div style="background:{style.bg};border:1px solid {style.border};border-radius:10px;padding:16px 18px;">
      <div style="color:{style.text};font-weight:700;font-size:14px;margin-bottom:8px;">{style.headline}</div>
      <table style="width:100%;border-collapse:collapse;">{table}</table>
    </div>
    {_html_button(link)}
  </div>

  <div style="padding:14px 26px;border-top:1px solid #eef2f7;color:#94a3b8;font-size:11px;text-align:center;">
    CERTWATCH &middot; website uptime monitoring &middot; by Workmates
  </div>
</div>"""
