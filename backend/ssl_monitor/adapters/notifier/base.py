"""Notifier port and shared message formatters.

Pure formatters reused by every notifier:
  * ``format_subject`` / ``format_body`` — plain text (console logs, SNS, and the
    text/fallback part of SES). SNS email is plain-text only (an AWS limit).
  * ``format_html_body`` — a branded HTML card for SES ``Body.Html`` so the email
    looks like a real alert, not a debug line.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from ...core.models import Alert


@runtime_checkable
class NotifierPort(Protocol):
    def notify(self, alert: Alert) -> None:
        """Deliver an alert. Implementations must not raise on delivery failure
        in a way that aborts a checker run — log and continue."""
        ...


def format_subject(alert: Alert) -> str:
    return f"[SSL {alert.status.value.upper()}] {alert.domain}"


def format_body(alert: Alert) -> str:
    parts = [
        f"domain={alert.domain}",
        f"status={alert.status.value}",
        f"days_remaining={alert.days_remaining}",
        f"threshold={alert.threshold}",
    ]
    if alert.not_after is not None:
        parts.append(f"not_after={alert.not_after.isoformat()}")
    return " ".join(parts)


# status → (text colour, bg, border, headline)
_ALERT_PALETTE = {
    "expired":     ("#991b1b", "#fef2f2", "#fecaca", "CERTIFICATE EXPIRED"),
    "warning":     ("#92400e", "#fffbeb", "#fde68a", "CERTIFICATE EXPIRING SOON"),
    "unreachable": ("#374151", "#f3f4f6", "#e5e7eb", "HOST UNREACHABLE"),
    "ok":          ("#166534", "#f0fdf4", "#bbf7d0", "CERTIFICATE OK"),
}


def format_html_body(alert: Alert) -> str:
    """A self-contained, email-client-safe HTML card (inline styles only)."""
    status = alert.status.value
    text_c, bg_c, border_c, headline = _ALERT_PALETTE.get(status, _ALERT_PALETTE["unreachable"])

    days = alert.days_remaining if alert.days_remaining is not None else "—"
    expiry = alert.not_after.strftime("%d %b %Y, %H:%M UTC") if alert.not_after else "—"
    threshold = f"{alert.threshold} days" if alert.threshold is not None else "—"
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    if status == "expired":
        action = ("Renew the certificate immediately — it has already expired and "
                  "visitors will see security warnings.")
    elif status == "unreachable":
        action = ("Check the host, port, and network — the TLS endpoint could not be "
                  "reached, so expiry cannot be verified.")
    else:
        action = f"Renew before {expiry} to avoid an outage."

    def row(label: str, value: object, mono: bool = False) -> str:
        vfont = "font-family:ui-monospace,Menlo,monospace;" if mono else ""
        return (
            '<tr>'
            f'<td style="padding:7px 0;color:#64748b;font-size:13px;width:150px;">{label}</td>'
            f'<td style="padding:7px 0;color:#1e293b;font-size:13px;font-weight:600;{vfont}">{value}</td>'
            '</tr>'
        )

    def badge(text: str) -> str:
        return (
            '<span style="display:inline-block;background:rgba(255,255,255,0.18);color:#fff;'
            'font-size:11px;letter-spacing:.5px;padding:3px 10px;border-radius:20px;'
            f'margin:0 6px 6px 0;">{text}</span>'
        )

    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:linear-gradient(135deg,#6c5cff 0%,#4733e0 55%,#2a1e7a 100%);padding:22px 26px;color:#fff;">
    <div style="font-size:11px;letter-spacing:2px;opacity:.85;text-transform:uppercase;">CERTWATCH · TLS Expiry Alert</div>
    <div style="font-size:20px;font-weight:700;margin:8px 0 12px;font-family:ui-monospace,Menlo,monospace;">{alert.domain}</div>
    <div>{badge(headline)}{badge(f"{days} days left")}{badge(f"threshold {threshold}")}</div>
    <div style="font-size:11px;opacity:.8;margin-top:10px;">Generated {generated}</div>
  </div>

  <div style="padding:22px 26px;">
    <div style="background:{bg_c};border:1px solid {border_c};border-radius:10px;padding:16px 18px;">
      <div style="color:{text_c};font-weight:700;font-size:14px;margin-bottom:8px;">&#9888;&#65039; {headline}</div>
      <table style="width:100%;border-collapse:collapse;">
        {row("Domain", alert.domain, mono=True)}
        {row("Status", status.upper())}
        {row("Days remaining", days)}
        {row("Expires", expiry)}
        {row("Alert threshold", threshold)}
      </table>
    </div>
    <p style="color:#334155;font-size:13px;line-height:1.6;margin:16px 2px 0;">
      <strong>Recommended action:</strong> {action}
    </p>
  </div>

  <div style="padding:14px 26px;border-top:1px solid #eef2f7;color:#94a3b8;font-size:11px;text-align:center;">
    CERTWATCH &middot; TLS certificate surveillance &middot; by Workmates
  </div>
</div>"""
