"""PostgreSQL side of the hourly rollup + raw-result cleanup (see ``core.rollup``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.tables import check_results, check_results_hourly


class PostgresRollupRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def last_rolled_hour(self) -> datetime | None:
        with self._engine.connect() as conn:
            return conn.execute(select(func.max(check_results_hourly.c.hour_start))).scalar()

    def earliest_result(self) -> datetime | None:
        with self._engine.connect() as conn:
            return conn.execute(select(func.min(check_results.c.checked_at))).scalar()

    def roll_up(self, start: datetime, end: datetime) -> int:
        """(Re)write the hourly rows for results in [start, end). Returns rows written."""
        # Hours are cut in UTC (as core.rollup does) whatever the session time
        # zone is; in IST (+05:30) a plain date_trunc would cut at :30 UTC.
        hour = func.date_trunc("hour", check_results.c.checked_at, "UTC")
        summary = (
            select(
                check_results.c.monitor_id,
                hour.label("hour_start"),
                func.count().label("total_checks"),
                func.count().filter(check_results.c.is_up).label("up_checks"),
                func.avg(check_results.c.response_ms).label("avg_response_ms"),
                func.min(check_results.c.response_ms).label("min_response_ms"),
                func.max(check_results.c.response_ms).label("max_response_ms"),
            )
            .where(check_results.c.checked_at >= start, check_results.c.checked_at < end)
            .group_by(check_results.c.monitor_id, hour)
        )
        columns = ["monitor_id", "hour_start", "total_checks", "up_checks",
                   "avg_response_ms", "min_response_ms", "max_response_ms"]
        stmt = pg_insert(check_results_hourly).from_select(columns, summary)
        stmt = stmt.on_conflict_do_update(
            index_elements=["monitor_id", "hour_start"],
            set_={c: stmt.excluded[c] for c in columns[2:]},
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).rowcount

    def delete_results_before(self, cutoff: datetime, limit: int) -> int:
        """Delete up to ``limit`` raw results older than ``cutoff``. Returns rows deleted.

        Small batches keep each transaction (and its locks) short while the
        uptime worker keeps writing new results.
        """
        oldest = (
            select(check_results.c.id)
            .where(check_results.c.checked_at < cutoff)
            .limit(limit)
            .scalar_subquery()
        )
        with self._engine.begin() as conn:
            return conn.execute(delete(check_results).where(check_results.c.id.in_(oldest))).rowcount
