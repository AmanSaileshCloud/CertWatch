"""Copy existing CertWatch domains from the legacy SQLite file into PostgreSQL.

Safe to re-run: rows already present in Postgres are left untouched, so a second
run never overwrites newer data. Run ``alembic upgrade head`` first.

Usage:
  python -m backend.scripts.migrate_sqlite_to_postgres --sqlite certwatch.db
  python -m backend.scripts.migrate_sqlite_to_postgres --sqlite certwatch.db --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from ..ssl_monitor.adapters.storage.postgres import PostgresStorage
from ..ssl_monitor.adapters.storage.sqlite import SqliteStorage


@dataclass(frozen=True)
class MigrationReport:
    found: int
    copied: int
    skipped: int


def migrate_domains(
    source: SqliteStorage, target: PostgresStorage, *, dry_run: bool = False
) -> MigrationReport:
    records = source.list()
    if dry_run:
        existing = sum(1 for r in records if target.get(r.domain) is not None)
        return MigrationReport(found=len(records), copied=len(records) - existing,
                               skipped=existing)
    copied = sum(1 for r in records if target.insert_if_absent(r))
    return MigrationReport(found=len(records), copied=copied, skipped=len(records) - copied)


def main(argv: list[str] | None = None) -> int:
    from ..ssl_monitor.handlers._deps import get_settings, make_engine

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", default=None, help="path to certwatch.db "
                        "(default: SQLITE_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args(argv)

    settings = get_settings()
    path = args.sqlite or settings.sqlite_path
    if not os.path.exists(path):
        print(f"SQLite file not found: {path}", file=sys.stderr)
        return 1

    report = migrate_domains(SqliteStorage(path), PostgresStorage(make_engine(settings)),
                             dry_run=args.dry_run)
    verb = "would copy" if args.dry_run else "copied"
    print(f"domains found={report.found} {verb}={report.copied} "
          f"already-present={report.skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
