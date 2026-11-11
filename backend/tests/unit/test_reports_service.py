"""services.reports + report_csv with the in-memory stores."""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import replace
from functools import partial

import pytest

from backend.ssl_monitor.adapters.storage.catalog_memory import MemoryCatalogRepo
from backend.ssl_monitor.adapters.storage.uptime_memory import MemoryUptimeRepo
from backend.ssl_monitor.core.incident_rules import evaluate
from backend.ssl_monitor.core.reports import Granularity
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor
from backend.ssl_monitor.services.errors import InvalidInput
from backend.ssl_monitor.services.report_csv import downtime_csv, uptime_csv
from backend.ssl_monitor.services.reports import downtime_log, uptime_report

UTC = dt.timezone.utc
TZ = "Asia/Kolkata"
D = dt.date
# Report on 1–2 Nov 2026 (IST). 1 Nov 00:00 IST = 31 Oct 18:30 UTC.
DAY1 = dt.datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
NOW = dt.datetime(2026, 11, 5, tzinfo=UTC)


def _add(repo: MemoryUptimeRepo, monitor: Monitor) -> Monitor:
    """Add a monitor created 10 days before the report (stores stamp the real clock)."""
    stored = repo.add_monitor(monitor)
    stored = replace(stored, created_at=DAY1 - dt.timedelta(days=10))
    repo._monitors[stored.id] = stored
    return stored


@pytest.fixture
def stores():
    repo, catalog = MemoryUptimeRepo(), MemoryCatalogRepo()
    acme = catalog.add_customer("Acme")
    shops = catalog.add_group(acme.id, "Shops")
    shop = _add(repo, Monitor(id=0, name="Shop", url="https://shop.acme.test",
                              interval_seconds=60, customer_id=acme.id, group_id=shops.id))
    blog = _add(repo, Monitor(id=0, name="=Blog", url="https://blog.test", interval_seconds=60))
    return repo, catalog, shop, blog


def _checks(repo, monitor_id: int, at: dt.datetime, *ups: bool, ms: int = 200) -> None:
    rules = partial(evaluate, failure_threshold=3, recovery_threshold=1)
    for i, up in enumerate(ups):
        repo.record_checks([CheckResult(monitor_id=monitor_id,
                                        checked_at=at + dt.timedelta(minutes=i),
                                        is_up=up, response_ms=ms if up else None)], rules)


def _report(repo, catalog, granularity=Granularity.DAY, **kw):
    return uptime_report(repo, catalog, first=D(2026, 11, 1), last=D(2026, 11, 2),
                         granularity=granularity, tz=TZ, now=NOW, **kw)


def test_an_outage_lowers_uptime_only_on_its_day(stores) -> None:
    repo, catalog, shop, _ = stores
    # Down from 10:00 to 10:36 IST on 1 Nov (first failure → recovery = 36 minutes).
    outage = DAY1 + dt.timedelta(hours=10)
    _checks(repo, shop.id, outage, *([False] * 36), True)
    report = _report(repo, catalog)
    shop_row = next(m for m in report.monitors if m.monitor_id == shop.id)
    day1, day2 = shop_row.periods
    assert day1.downtime_seconds == 36 * 60
    assert day1.uptime_pct == round(100 * (1 - 36 * 60 / 86400), 3)
    assert day1.incidents == 1
    assert day1.checks == 37 and day1.failed_checks == 36 and day1.avg_response_ms == 200
    assert (day2.uptime_pct, day2.downtime_seconds, day2.incidents) == (100.0, 0, 0)
    assert shop_row.total.downtime_seconds == 36 * 60
    assert (shop_row.customer_name, shop_row.group_name) == ("Acme", "Shops")


def test_customer_uptime_is_time_weighted_across_its_websites(stores) -> None:
    repo, catalog, shop, blog = stores
    _checks(repo, shop.id, DAY1 + dt.timedelta(hours=1), *([False] * 60), True)
    report = _report(repo, catalog, Granularity.TOTAL)
    by_name = {c.customer_name: c for c in report.customers}
    assert set(by_name) == {"Acme", "No customer"}  # blog has no customer
    assert by_name["Acme"].monitors == 1
    assert by_name["Acme"].total.downtime_seconds == 3600
    assert by_name["Acme"].total.uptime_pct == round(100 * (1 - 3600 / (2 * 86400)), 3)
    assert by_name["No customer"].total.uptime_pct == 100.0


def test_filter_by_customer(stores) -> None:
    repo, catalog, shop, _ = stores
    report = _report(repo, catalog, customer_id=shop.customer_id)
    assert [m.name for m in report.monitors] == ["Shop"]


def test_invalid_range_is_rejected(stores) -> None:
    repo, catalog, *_ = stores
    with pytest.raises(InvalidInput, match="before the start"):
        uptime_report(repo, catalog, first=D(2026, 11, 2), last=D(2026, 11, 1),
                      granularity=Granularity.DAY, tz=TZ, now=NOW)


def test_downtime_log_lists_outages_newest_first(stores) -> None:
    repo, catalog, shop, blog = stores
    _checks(repo, shop.id, DAY1 + dt.timedelta(hours=1), *([False] * 5), True)
    _checks(repo, blog.id, DAY1 + dt.timedelta(hours=5), False, False, False)  # still down
    log = downtime_log(repo, catalog, first=D(2026, 11, 1), last=D(2026, 11, 2), tz=TZ, now=NOW)
    assert [e.monitor_name for e in log] == ["=Blog", "Shop"]
    assert log[0].resolved_at is None
    # Still down: counted up to the end of the report range (2 Nov 24:00 IST).
    assert log[0].downtime_seconds == 2 * 86400 - 5 * 3600
    assert log[1].downtime_seconds == 5 * 60


def _rows(text: str) -> list[list[str]]:
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


def test_uptime_csv_has_one_row_per_website_and_period(stores) -> None:
    repo, catalog, *_ = stores
    rows = _rows(uptime_csv(_report(repo, catalog), "monitor"))
    assert rows[0][:5] == ["Customer", "Group", "Website", "URL", "Period"]
    assert len(rows) == 1 + 2 * 2
    assert rows[1][:5] == ["Acme", "Shops", "Shop", "https://shop.acme.test", "2026-11-01"]


def test_csv_neutralises_spreadsheet_formulas(stores) -> None:
    repo, catalog, *_ = stores
    rows = _rows(uptime_csv(_report(repo, catalog), "monitor"))
    assert ["'=Blog"] == [r[2] for r in rows if "Blog" in r[2]][:1]


def test_customer_csv_and_downtime_csv(stores) -> None:
    repo, catalog, shop, _ = stores
    _checks(repo, shop.id, DAY1 + dt.timedelta(hours=1), *([False] * 5), True)
    customer_rows = _rows(uptime_csv(_report(repo, catalog, Granularity.TOTAL), "customer"))
    assert customer_rows[0][:3] == ["Customer", "Websites", "Period"]
    assert [r[0] for r in customer_rows[1:]] == ["Acme", "No customer"]
    log = downtime_log(repo, catalog, first=D(2026, 11, 1), last=D(2026, 11, 2), tz=TZ, now=NOW)
    down_rows = _rows(downtime_csv(log, TZ))
    assert down_rows[1][4] == "2026-11-01 01:00:00"  # local IST time
    assert down_rows[1][6] == "300"
