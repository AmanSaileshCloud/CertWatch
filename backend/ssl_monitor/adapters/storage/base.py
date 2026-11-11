"""Storage port — the interface the API, runner, and handlers depend on.

Defined as a ``Protocol`` so any implementation (SQLite, in-memory fake) is
accepted without inheritance. Core never imports this; only adapters and the
wiring layer do.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ...core.models import DomainRecord
from ...core.uptime_models import CheckResult, Incident, Monitor, MonitorTransition

# (current monitor, result) -> transition; the incident rules with thresholds bound.
IncidentRules = Callable[[Monitor, CheckResult], MonitorTransition]


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

    def record_checks(self, results: Sequence[CheckResult], rules: IncidentRules) -> list[Incident]:
        """Persist one cycle's outcome in ONE transaction.

        Each monitor row is locked and re-read, and ``rules(current_monitor, result)``
        is applied to the CURRENT stored state — never to the snapshot taken at claim
        time — so a pause or edit made while the check was in flight is respected.
        Results for monitors that were deleted or paused meanwhile are dropped.
        For the rest:

        * insert the result into check_results;
        * copy it onto the monitor's ``last_*`` columns, and set ``state``,
          ``consecutive_failures``, ``consecutive_successes``, ``first_failure_at``
          from the transition;
        * ``WENT_DOWN``: open an incident (``started_at = first_failure_at``,
          ``reason`` = the error of the check that confirmed the outage) unless one
          is already open for the monitor;
        * ``RECOVERED``: close the open incident (``resolved_at = result.checked_at``,
          ``duration_seconds`` = whole seconds since ``started_at``); no-op if none open.

        Returns the incidents opened or closed by this call, in result order."""
        ...

    def list_incidents(self, monitor_id: int | None = None, limit: int = 100) -> list[Incident]:
        """Incidents newest-started first; all monitors when ``monitor_id`` is None."""
        ...

    def list_check_results(self, monitor_id: int, limit: int = 100) -> list[CheckResult]:
        """Most recent results for one monitor, newest first."""
        ...
