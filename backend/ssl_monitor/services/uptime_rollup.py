"""Hourly uptime summaries + raw-result cleanup (one run of the rollup job)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..core.rollup import chunks, deletion_cutoff, rollup_window

logger = logging.getLogger("ssl_monitor.uptime.rollup")

DELETE_BATCH = 10_000


class RollupRepo(Protocol):
    def last_rolled_hour(self) -> datetime | None: ...
    def earliest_result(self) -> datetime | None: ...
    def roll_up(self, start: datetime, end: datetime) -> int: ...
    def delete_results_before(self, cutoff: datetime, limit: int) -> int: ...


@dataclass(frozen=True)
class RollupSummary:
    hourly_rows: int
    deleted: int
    summarised_until: datetime | None


def run_rollup(
    repo: RollupRepo, *, now: datetime, retention_days: int, delete_batch: int = DELETE_BATCH
) -> RollupSummary:
    """Summarise finished hours, then delete raw results past retention."""
    window = rollup_window(repo.last_rolled_hour(), repo.earliest_result(), now)
    rows = 0
    if window:
        for start, end in chunks(*window):
            rows += repo.roll_up(start, end)
        summarised_until = window[1]
    else:
        summarised_until = _already_summarised_until(repo)

    deleted = 0
    cutoff = deletion_cutoff(now, retention_days, summarised_until)
    if cutoff is not None:
        while True:
            batch = repo.delete_results_before(cutoff, delete_batch)
            deleted += batch
            if batch < delete_batch:
                break
    logger.info("Rollup: %d hourly rows written, %d old raw results deleted", rows, deleted)
    return RollupSummary(hourly_rows=rows, deleted=deleted, summarised_until=summarised_until)


def _already_summarised_until(repo: RollupRepo) -> datetime | None:
    """With nothing new to summarise, everything before the last summarised hour is done."""
    return repo.last_rolled_hour()
