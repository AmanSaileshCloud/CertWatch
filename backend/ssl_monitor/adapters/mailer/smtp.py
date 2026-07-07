"""SMTP mailer — sends the daily digest via any SMTP provider.

Provider-agnostic: works with any SMTP relay (Brevo, Mailjet, SendGrid, Gmail,
…) — switching providers is config-only, no code change. Uses Python's built-in
smtplib so no extra dependency is needed. Configure via environment variables:
    SMTP_HOST      e.g. smtp-relay.brevo.com
    SMTP_PORT      587 (STARTTLS) or 465 (SSL)
    SMTP_USER      SMTP login / username for the provider
    SMTP_PASS      SMTP key or app-specific password
    SMTP_FROM      display name + address, e.g. "CERTWatch <alerts@company.com>"
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("ssl_monitor.mailer")

# Cap every SMTP network step so an unreachable/slow mail server can never hang
# a request thread indefinitely (smtplib blocks on the OS default otherwise).
_SMTP_TIMEOUT_SECONDS = 10


class SmtpMailer:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender

    def _deliver(self, email: str, msg: MIMEMultipart, *, kind: str) -> None:
        """Send one already-built message. Single place that touches the network."""
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
                    smtp.login(self.username, self.password)
                    smtp.sendmail(self.username, email, msg.as_string())
            else:
                with smtplib.SMTP(self.host, self.port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
                    smtp.ehlo()
                    smtp.starttls()
                    smtp.login(self.username, self.password)
                    smtp.sendmail(self.username, email, msg.as_string())
            logger.info("%s email sent to %s", kind, email)
        except Exception as exc:
            logger.error("Failed to send %s email to %s: %s", kind, email, exc)
            raise

    def send_digest(self, email: str, domains: list) -> None:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%A, %d %b %Y")

        status_colors = {
            "ok":          ("#166534", "#f0fff4", "#86efac"),
            "warning":     ("#92400e", "#fffbeb", "#fcd34d"),
            "expired":     ("#991b1b", "#fef2f2", "#fca5a5"),
            "unreachable": ("#374151", "#f9fafb", "#d1d5db"),
        }

        counts: dict[str, int] = {"ok": 0, "warning": 0, "expired": 0, "unreachable": 0}
        for d in domains:
            counts[d.status.value] = counts.get(d.status.value, 0) + 1

        def chip(label: str, count: int, key: str) -> str:
            text_c, bg_c, border_c = status_colors.get(key, ("#374151", "#f9fafb", "#d1d5db"))
            return (
                f'<div style="background:{bg_c};border:1px solid {border_c};border-radius:8px;'
                f'padding:12px 18px;text-align:center;min-width:80px;">'
                f'<div style="font-size:22px;font-weight:700;color:{text_c};">{count}</div>'
                f'<div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{text_c};">{label}</div>'
                f"</div>"
            )

        summary_chips = "".join([
            chip("Healthy",     counts["ok"],          "ok"),
            chip("Warning",     counts["warning"],     "warning"),
            chip("Expired",     counts["expired"],     "expired"),
            chip("Unreachable", counts["unreachable"], "unreachable"),
        ])

        def row(d) -> str:  # type: ignore[type-arg]
            status_val = d.status.value
            text_c, bg_c, _ = status_colors.get(status_val, ("#374151", "#f9fafb", "#d1d5db"))
            expiry = d.not_after.strftime("%d %b %Y") if d.not_after else "—"
            days = str(d.days_remaining) if d.days_remaining is not None else "—"
            badge = (
                f'<span style="background:{bg_c};color:{text_c};padding:2px 8px;'
                f'border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;">'
                f"{status_val}</span>"
            )
            port_str = f":{d.port}" if d.port != 443 else ""
            return (
                f"<tr style='border-bottom:1px solid #e2e8f0;'>"
                f"<td style='padding:11px 14px;font-family:monospace;font-size:13px;color:#1e293b;'>"
                f"{d.host}{port_str}</td>"
                f"<td style='padding:11px 14px;'>{badge}</td>"
                f"<td style='padding:11px 14px;font-size:13px;color:#475569;'>{expiry}</td>"
                f"<td style='padding:11px 14px;font-size:13px;color:#475569;text-align:right;'>{days}</td>"
                f"</tr>"
            )

        sorted_domains = sorted(
            domains,
            key=lambda d: ({"expired": 0, "warning": 1, "unreachable": 2, "ok": 3}.get(d.status.value, 4), d.days_remaining or 9999),
        )
        rows_html = "".join(row(d) for d in sorted_domains)

        plain_lines = [f"CERTWatch Daily Digest — {date_str}", ""]
        for d in sorted_domains:
            port_str = f":{d.port}" if d.port != 443 else ""
            plain_lines.append(f"  {d.host}{port_str}  [{d.status.value.upper()}]  expires={d.not_after}  days={d.days_remaining}")
        plain = "\n".join(plain_lines)

        html = f"""
        <div style="font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;padding:32px;background:#fff;">
          <h2 style="margin:0 0 4px;color:#6c5cff;font-size:20px;">CERTWatch</h2>
          <p style="margin:0 0 24px;color:#64748b;font-size:13px;">Daily Digest — {date_str}</p>

          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:28px;">{summary_chips}</div>

          <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
            <thead>
              <tr style="background:#f8fafc;">
                <th style="text-align:left;padding:10px 14px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;font-weight:500;">Domain</th>
                <th style="text-align:left;padding:10px 14px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;font-weight:500;">Status</th>
                <th style="text-align:left;padding:10px 14px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;font-weight:500;">Expires</th>
                <th style="text-align:right;padding:10px 14px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;font-weight:500;">Days left</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>

          <p style="margin:24px 0 0;font-size:12px;color:#94a3b8;text-align:center;">
            CERTWatch by Workmates · TLS Expiry Surveillance
          </p>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"CERTWatch Daily Digest — {date_str} · {len(domains)} domains"
        msg["From"] = self.sender
        msg["To"] = email
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))
        self._deliver(email, msg, kind="Digest")
