"""Digest renderer — builds the branded HTML status report.

The digest is a point-in-time snapshot of every monitored domain's stored
status. It is rendered here and downloaded by the operator from the dashboard
(no email is sent). Keeping the rendering in one pure function makes it trivial
to test and reuse.
"""

from __future__ import annotations

from datetime import datetime, timezone

# status.value -> (text, background, border)
_STATUS_COLORS: dict[str, tuple[str, str, str]] = {
    "ok": ("#166534", "#f0fff4", "#86efac"),
    "warning": ("#92400e", "#fffbeb", "#fcd34d"),
    "expired": ("#991b1b", "#fef2f2", "#fca5a5"),
    "unreachable": ("#374151", "#f9fafb", "#d1d5db"),
}
_FALLBACK = ("#374151", "#f9fafb", "#d1d5db")
_SORT_ORDER = {"expired": 0, "warning": 1, "unreachable": 2, "ok": 3}


def render_digest_html(domains: list) -> str:
    """Render the full digest as a self-contained HTML document."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %d %b %Y")

    counts: dict[str, int] = {"ok": 0, "warning": 0, "expired": 0, "unreachable": 0}
    for d in domains:
        counts[d.status.value] = counts.get(d.status.value, 0) + 1

    def chip(label: str, count: int, key: str) -> str:
        text_c, bg_c, border_c = _STATUS_COLORS.get(key, _FALLBACK)
        return (
            f'<div style="background:{bg_c};border:1px solid {border_c};border-radius:8px;'
            f'padding:12px 18px;text-align:center;min-width:80px;">'
            f'<div style="font-size:22px;font-weight:700;color:{text_c};">{count}</div>'
            f'<div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{text_c};">{label}</div>'
            f"</div>"
        )

    summary_chips = "".join([
        chip("Healthy", counts["ok"], "ok"),
        chip("Warning", counts["warning"], "warning"),
        chip("Expired", counts["expired"], "expired"),
        chip("Unreachable", counts["unreachable"], "unreachable"),
    ])

    def row(d) -> str:  # type: ignore[type-arg]
        status_val = d.status.value
        text_c, bg_c, _ = _STATUS_COLORS.get(status_val, _FALLBACK)
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
        key=lambda d: (_SORT_ORDER.get(d.status.value, 4), d.days_remaining if d.days_remaining is not None else 9999),
    )
    rows_html = "".join(row(d) for d in sorted_domains)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CERTWatch Digest — {date_str}</title>
</head>
<body style="margin:0;background:#eef2f7;">
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
</body>
</html>"""
