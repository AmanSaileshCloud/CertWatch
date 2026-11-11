"""/reports endpoints (in-memory stores)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


def _today() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Kolkata")).date()


def test_uptime_report_defaults_to_the_last_30_days(api) -> None:
    customer = api.customer()
    api.monitor(customer_id=customer["id"])
    r = api.client.get("/reports/uptime")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last"] == _today().isoformat()
    assert len(body["periods"]) == 30
    assert body["timezone"] == "Asia/Kolkata"
    [row] = body["monitors"]
    assert row["customer_name"] == "Acme Corp"
    assert len(row["periods"]) == 30
    assert [c["customer_name"] for c in body["customers"]] == ["Acme Corp"]


def test_uptime_report_monthly_for_one_customer(api) -> None:
    acme = api.customer("Acme")
    other = api.customer("Other")
    api.monitor(name="Acme shop", customer_id=acme["id"])
    api.monitor(name="Other shop", url="https://other.example", customer_id=other["id"])
    today = _today()
    r = api.client.get("/reports/uptime", params={
        "start": (today - dt.timedelta(days=60)).isoformat(), "end": today.isoformat(),
        "granularity": "month", "customer_id": acme["id"]})
    assert r.status_code == 200, r.text
    assert [m["name"] for m in r.json()["monitors"]] == ["Acme shop"]
    assert 2 <= len(r.json()["periods"]) <= 3


def test_bad_range_is_a_422_with_a_readable_message(api) -> None:
    r = api.client.get("/reports/uptime", params={"start": "2026-11-05", "end": "2026-11-01"})
    assert r.status_code == 422
    assert "before the start date" in r.text


def test_uptime_csv_download(api) -> None:
    api.monitor()
    r = api.client.get("/reports/uptime.csv", params={"granularity": "total", "level": "customer"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "uptime-by-customer" in r.headers["content-disposition"]
    assert r.text.lstrip("﻿").startswith("Customer,Websites,Period,Uptime %")


def test_downtime_log_and_csv(api) -> None:
    monitor = api.monitor()
    api.take_down(monitor["id"])
    r = api.client.get("/reports/downtime")
    assert r.status_code == 200
    [entry] = r.json()
    assert entry["monitor_name"] == "Shop" and entry["resolved_at"] is None
    csv_text = api.client.get("/reports/downtime.csv").text
    assert "still down" in csv_text
