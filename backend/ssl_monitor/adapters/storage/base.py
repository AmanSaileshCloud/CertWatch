"""Storage port — the interface the API, runner, and handlers depend on.

Defined as a ``Protocol`` so any implementation (SQLite, in-memory fake) is
accepted without inheritance. Core never imports this; only adapters and the
wiring layer do.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ...core.models import DomainRecord
from ...core.uptime_models import CheckResult, Monitor


@runtime_checkable
class StoragePort(Protocol):
    def put(self, record: DomainRecord) -> None:
        """Insert or overwrite a domain record by its ``domain`` key."""
        ...

    def get(self, domain: str) -> DomainRecord | None:
        """Return the record for ``domain`` or None if absent."""
        ...

    def list(self) -> list[DomainRecord]:
        """Return all records (table scan; fine for the dashboard's scale)."""
        ...

    def delete(self, domain: str) -> None:
        """Delete a record by key; a no-op if it does not exist."""
        ...


@runtime_checkable
class UptimeRepoPort(Protocol):
    """Persistence for uptime monitors and their check results."""

    def add_monitor(self, monitor: Monitor) -> Monitor:
        """Insert a monitor (its ``id`` is ignored) and return it with the new id."""
        ...

    def get_monitor(self, monitor_id: int) -> Monitor | None:
        ...

    def claim_due_monitors(self, now: datetime, limit: int) -> list[Monitor]:
        """Return up to ``limit`` unpaused monitors whose ``next_check_at <= now``,
        oldest-due first, and in the same transaction move each one's
        ``next_check_at`` to ``now + interval_seconds`` so a concurrent worker
        never claims the same monitor. Returned monitors carry the *old* values."""
        ...

    def save_check_results(self, results: Sequence[CheckResult]) -> None:
        """Insert the results and copy each one onto its monitor's ``last_*``
        columns (``last_checked_at``, ``last_status_code``, ``last_response_ms``,
        ``last_error``). Results for monitors deleted meanwhile are dropped."""
        ...

    def list_check_results(self, monitor_id: int, limit: int = 100) -> list[CheckResult]:
        """Most recent results for one monitor, newest first."""
        ...
