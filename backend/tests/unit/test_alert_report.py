"""Unit tests for the alert email's availability / performance report."""

from __future__ import annotations

import datetime as dt

from backend.ssl_monitor.core.uptime_models import CheckResult, Incident, Monitor
from backend.ssl_monitor.services.alert_report import build_report, resolve_host_ip

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 12, 6, 0, tzinfo=UTC)
MONITOR = Monitor(id=1, name="Shop", url="https://shop.example.com", interval_seconds=300)


class FakeRepo:
    def __init__(self, *, counts=None, incidents=(), results=(), broken=False):
        self.counts, self.incidents, self.results, self.broken = counts, incidents, results, broken

    def uptime_counts(self, since, monitor_ids=None):
        if self.broken:
            raise RuntimeError("db down")
        return {1: self.counts} if self.counts else {}

    def list_incidents(self, monitor_id=None, limit=100):
        return list(self.incidents)

    def list_check_results(self, monitor_id, limit=100, since=None):
        return list(self.results)


def _result(minutes_ago: int, up: bool, ms: int | None) -> CheckResult:
    return CheckResult(monitor_id=1, checked_at=NOW - dt.timedelta(minutes=minutes_ago),
                       is_up=up, response_ms=ms)


def test_report_figures():
    incidents = [
        # closed inside the window: 41 s of downtime
        Incident(id=1, monitor_id=1, started_at=NOW - dt.timedelta(hours=5),
                 resolved_at=NOW - dt.timedelta(hours=5) + dt.timedelta(seconds=41)),
        # long before the window: not counted
        Incident(id=2, monitor_id=1, started_at=NOW - dt.timedelta(days=9),
                 resolved_at=NOW - dt.timedelta(days=8)),
        # still open: counted up to now
        Incident(id=3, monitor_id=1, started_at=NOW - dt.timedelta(minutes=2)),
    ]
    results = [_result(1, False, None), _result(5, True, 100), _result(10, True, 300)]
    repo = FakeRepo(counts=(1000, 999), incidents=incidents, results=results)

    report = build_report(repo, MONITOR, NOW, resolve_ip=lambda url: "3.6.142.81")

    assert report is not None
    assert report.resolved_ip == "3.6.142.81"
    assert report.availability_pct == 99.9
    assert report.downtimes == 2
    assert report.downtime_seconds == 41 + 120
    assert report.checks == 3
    assert round(report.uptime_pct, 2) == 66.67
    assert (report.avg_response_ms, report.min_response_ms, report.max_response_ms) == (200, 100, 300)


def test_no_history_gives_empty_figures():
    report = build_report(FakeRepo(), MONITOR, NOW)
    assert report is not None
    assert report.availability_pct is None and report.avg_response_ms is None
    assert report.downtimes == 0 and report.resolved_ip is None


def test_database_failure_gives_no_report():
    assert build_report(FakeRepo(broken=True), MONITOR, NOW) is None


def test_resolve_host_ip_without_host():
    assert resolve_host_ip("not a url") is None


def test_resolve_host_ip_literal():
    assert resolve_host_ip("http://127.0.0.1:8080/x") == "127.0.0.1"
