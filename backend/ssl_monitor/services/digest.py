"""Digest report — renders every monitored domain's status as a PDF.

The report is a point-in-time snapshot of each domain's stored status,
downloaded on demand by an admin from the dashboard (no email is sent).
Built with reportlab (pure Python — installs on the t2.micro with no system
libraries).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# status.value -> (text colour, fill colour)
_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "ok": ("#166534", "#f0fff4"),
    "warning": ("#92400e", "#fffbeb"),
    "expired": ("#991b1b", "#fef2f2"),
    "unreachable": ("#374151", "#f9fafb"),
}
_FALLBACK = ("#374151", "#f9fafb")
_SORT_ORDER = {"expired": 0, "warning": 1, "unreachable": 2, "ok": 3}
_ACCENT = colors.HexColor("#6c5cff")
_MUTED = colors.HexColor("#94a3b8")


def _sorted(domains: list) -> list:
    return sorted(
        domains,
        key=lambda d: (
            _SORT_ORDER.get(d.status.value, 4),
            d.days_remaining if d.days_remaining is not None else 9999,
        ),
    )


def render_digest_pdf(domains: list) -> bytes:
    """Render the status report to PDF bytes."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %d %b %Y · %H:%M UTC")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title="CERTWatch Certificate Status Report",
        author="CERTWatch by Workmates",
    )

    styles = {
        "brand": ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=20, textColor=_ACCENT),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=10, textColor=colors.HexColor("#64748b"), spaceBefore=2),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11, textColor=colors.HexColor("#1e293b"), spaceBefore=6, spaceAfter=6),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#1e293b")),
        "mono": ParagraphStyle("mono", fontName="Courier", fontSize=9, textColor=colors.HexColor("#1e293b")),
        "footer": ParagraphStyle("footer", fontName="Helvetica", fontSize=8, textColor=_MUTED, alignment=1),
    }

    counts: dict[str, int] = {"ok": 0, "warning": 0, "expired": 0, "unreachable": 0}
    for d in domains:
        counts[d.status.value] = counts.get(d.status.value, 0) + 1

    story: list = [
        Paragraph("CERTWatch", styles["brand"]),
        Paragraph(f"Certificate Status Report — {date_str}", styles["sub"]),
        Spacer(1, 14),
    ]

    # ── Summary boxes ───────────────────────────────────────────────────────
    labels = [("Healthy", "ok"), ("Warning", "warning"), ("Expired", "expired"), ("Unreachable", "unreachable")]
    summary_cells = []
    for label, key in labels:
        text_c, fill_c = _STATUS_COLORS[key]
        summary_cells.append(
            Paragraph(
                f'<font size="16"><b>{counts[key]}</b></font><br/>'
                f'<font size="8" color="{text_c}">{label.upper()}</font>',
                ParagraphStyle("sumcell", fontName="Helvetica-Bold", fontSize=16,
                               textColor=colors.HexColor(text_c), alignment=1, leading=20),
            )
        )
    summary = Table([summary_cells], colWidths=[43 * mm] * 4, rowHeights=[20 * mm])
    summary_style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("INNERGRID", (0, 0), (-1, -1), 4, colors.white),
    ]
    for i, (_, key) in enumerate(labels):
        _, fill_c = _STATUS_COLORS[key]
        summary_style.append(("BACKGROUND", (i, 0), (i, 0), colors.HexColor(fill_c)))
        summary_style.append(("BOX", (i, 0), (i, 0), 0.5, colors.HexColor("#e2e8f0")))
    summary.setStyle(TableStyle(summary_style))
    story += [summary, Spacer(1, 16)]

    # ── Domain table ────────────────────────────────────────────────────────
    header = [
        Paragraph("<b>DOMAIN</b>", ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=_MUTED)),
        Paragraph("<b>STATUS</b>", ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=_MUTED)),
        Paragraph("<b>EXPIRES</b>", ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=_MUTED)),
        Paragraph("<b>DAYS LEFT</b>", ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=_MUTED, alignment=2)),
    ]
    rows = [header]
    sorted_domains = _sorted(domains)
    for d in sorted_domains:
        status_val = d.status.value
        text_c, _ = _STATUS_COLORS.get(status_val, _FALLBACK)
        port_str = f":{d.port}" if d.port != 443 else ""
        expiry = d.not_after.strftime("%d %b %Y") if d.not_after else "—"
        days = str(d.days_remaining) if d.days_remaining is not None else "—"
        rows.append([
            Paragraph(f"{d.host}{port_str}", styles["mono"]),
            Paragraph(f'<b>{status_val.upper()}</b>',
                      ParagraphStyle("stat", fontName="Helvetica-Bold", fontSize=8,
                                     textColor=colors.HexColor(text_c))),
            Paragraph(expiry, styles["cell"]),
            Paragraph(days, ParagraphStyle("days", fontName="Helvetica", fontSize=9,
                                           textColor=colors.HexColor("#475569"), alignment=2)),
        ])

    if len(rows) == 1:  # header only → no domains
        rows.append([Paragraph("No domains monitored.", styles["cell"]), "", "", ""])

    table = Table(rows, colWidths=[78 * mm, 30 * mm, 35 * mm, 31 * mm], repeatRows=1)
    table_style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f8fafc")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#e2e8f0")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]
    for r in range(1, len(rows)):
        table_style.append(("LINEBELOW", (0, r), (-1, r), 0.5, colors.HexColor("#eef2f7")))
    table.setStyle(TableStyle(table_style))
    story.append(table)

    def _footer(canvas, doc_):  # noqa: ANN001
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_MUTED)
        canvas.drawCentredString(
            A4[0] / 2, 10 * mm,
            f"CERTWatch by Workmates · TLS Expiry Surveillance   ·   Page {doc_.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
