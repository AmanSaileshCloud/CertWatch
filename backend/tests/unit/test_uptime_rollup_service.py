"""services.uptime_rollup with an in-memory fake repo."""

from __future__ import annotations

import datetime as dt

from backend.ssl_monitor.services.uptime_rollup import run_rollup

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 12, 30, tzinfo=UTC)


class FakeRepo:
    def __init__(self, *, last=None, earliest=None, old_rows=0) -> None:
        self.last, self.earliest, self.old_rows = last, earliest, old_rows
        self.rolled: list[tuple[dt.datetime, dt.datetime]] = []
        self.cutoffs: list[dt.datetime] = []

    def last_rolled_hour(self):
        return self.last

    def earliest_result(self):
        return self.earliest

    def roll_up(self, start, end):
        self.rolled.append((start, end))
        return 1

    def delete_results_before(self, cutoff, limit):
        self.cutoffs.append(cutoff)
        batch = min(limit, self.old_rows)
        self.old_rows -= batch
        return batch


def test_first_run_summarises_everything_in_daily_chunks_then_deletes_old_rows() -> None:
    repo = FakeRepo(earliest=NOW - dt.timedelta(days=45), old_rows=25)
    summary = run_rollup(repo, now=NOW, retention_days=30, delete_batch=10)
    assert repo.rolled[0][0] == dt.datetime(2026, 9, 27, 12, tzinfo=UTC)
    assert repo.rolled[-1][1] == dt.datetime(2026, 11, 11, 12, tzinfo=UTC)
    assert summary.hourly_rows == len(repo.rolled) == 45
    assert summary.deleted == 25
    assert repo.cutoffs == [NOW - dt.timedelta(days=30)] * 3  # batches of 10, 10, 5


def test_empty_database_does_nothing() -> None:
    repo = FakeRepo()
    summary = run_rollup(repo, now=NOW, retention_days=30)
    assert (summary.hourly_rows, summary.deleted) == (0, 0)
    assert repo.rolled == [] and repo.cutoffs == []
