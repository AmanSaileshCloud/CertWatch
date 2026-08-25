"""Tests for the PDF status report renderer."""

from __future__ import annotations

import datetime as dt

from backend.ssl_monitor.core.models import DomainRecord, Status
from backend.ssl_monitor.services.digest import render_digest_pdf

UTC = dt.timezone.utc


def _record(host, status, days, *, port=443):
    now = dt.datetime.now(UTC)
    return DomainRecord(
        domain=f"{host}:{port}",
        host=host,
        port=port,
        created_at=now,
        not_after=(now + dt.timedelta(days=days)) if days is not None else None,
        days_remaining=days,
        status=status,
    )


def test_render_returns_pdf_bytes():
    domains = [
        _record("good.example.com", Status.OK, 200),
        _record("warn.example.com", Status.WARNING, 5),
        _record("dead.example.com", Status.UNREACHABLE, None),
        _record("old.example.com", Status.EXPIRED, -3, port=8443),
    ]
    pdf = render_digest_pdf(domains)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-")   # valid PDF header
    assert len(pdf) > 1000


def test_render_handles_no_domains():
    pdf = render_digest_pdf([])
    assert pdf.startswith(b"%PDF-")
