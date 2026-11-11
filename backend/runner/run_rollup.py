"""Hourly uptime rollup + raw-result cleanup (one run, then exit).

Started every hour by certwatch-rollup.timer. Summarises each finished hour of
check results into ``check_results_hourly``, then deletes raw results older
than UPTIME_RAW_RETENTION_DAYS (default 30). Safe to run any number of times.

  python -m backend.runner.run_rollup
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from ..ssl_monitor.adapters.storage.rollup_postgres import PostgresRollupRepo
from ..ssl_monitor.handlers._deps import get_settings, make_engine
from ..ssl_monitor.services.uptime_rollup import run_rollup

logger = logging.getLogger("ssl_monitor.uptime.rollup")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    try:
        summary = run_rollup(
            PostgresRollupRepo(make_engine(settings)),
            now=datetime.now(timezone.utc),
            retention_days=settings.uptime_raw_retention_days,
        )
    except Exception as exc:
        logger.error("Rollup failed (is the database up?): %s", exc)
        return 1
    print(f"Hourly rows written: {summary.hourly_rows}, old raw results deleted: {summary.deleted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
