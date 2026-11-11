"""Storage port — the interface the API, runner, and handlers depend on.

Defined as a ``Protocol`` so any implementation (SQLite, in-memory fake) is
accepted without inheritance. Core never imports this; only adapters and the
wiring layer do.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ...core.alert_models import AlertCandidate, AlertKind, AlertRule
from ...core.app_settings_models import StoredSettings
from ...core.catalog_models import Customer, MonitorGroup
from ...core.models import DomainRecord
from ...core.uptime_models import CheckResult, CloseReason, Incident, Monitor, MonitorTransition

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
        Results for monitors that were deleted or paused meanwhile are dropped, and so
        is a result whose ``monitor_version`` is set but differs from the row's current
        ``updated_at`` (the monitor was edited while the check was in flight).
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

    def list_check_results(
        self, monitor_id: int, limit: int = 100, since: datetime | None = None
    ) -> list[CheckResult]:
        """Most recent results for one monitor, newest first; only ``checked_at >= since``
        when given."""
        ...

    def list_monitors(self) -> list[Monitor]:
        """Every monitor, ordered by name then id."""
        ...

    def change_monitor(
        self,
        monitor_id: int,
        values: Mapping[str, Any],
        *,
        now: datetime,
        reset_state: bool = False,
        close_reason: CloseReason | None = None,
    ) -> Monitor | None:
        """Apply a user change in ONE transaction (locking the monitor row, so it
        serialises with ``record_checks``) and return the updated monitor, or None if
        it does not exist.

        * ``values``: Monitor field names → new values (config fields, ``is_paused``,
          ``next_check_at``); ``updated_at`` is set to ``now``.
        * ``reset_state``: state → UNKNOWN, both counters → 0, first_failure_at → None.
        * ``close_reason``: close the open incident, if any, at ``now`` (duration in
          whole seconds) with that reason."""
        ...

    def delete_monitor(self, monitor_id: int) -> bool:
        """Delete a monitor (its results and incidents go with it). True if it existed."""
        ...

    def uptime_counts(
        self, since: datetime, monitor_ids: Collection[int] | None = None
    ) -> dict[int, tuple[int, int]]:
        """``{monitor_id: (total_checks, up_checks)}`` for checks at or after ``since``,
        only for ``monitor_ids`` when given (all monitors when None). Monitors with no
        checks in the window are absent."""
        ...

    def alert_candidates(self, now: datetime) -> list[AlertCandidate]:
        """Incidents that MAY need an alert, for monitors with ``alerts_enabled``:
        every open incident, plus incidents resolved within the last
        ``RECENTLY_CLOSED_WINDOW_HOURS`` whose ``recovery_alert_sent_at`` is NULL.
        Each carries its monitor and the customer/group names (one query, no N+1).
        The alert policy decides which of them are actually due."""
        ...

    def mark_alert_sent(self, incident_id: int, kind: AlertKind, at: datetime) -> None:
        """Record a sent alert: DOWN → down_alert_sent_at; REMINDER →
        last_reminder_sent_at and reminder_count + 1; RECOVERED → recovery_alert_sent_at.
        TEST is never recorded. Unknown incident → no-op."""
        ...

    def alert_dispatch_lock(self) -> AbstractContextManager[bool]:
        """A context manager guarding one alert-dispatch run across ALL workers; it
        yields True if this caller holds the lock for the ``with`` block, False if
        another worker is dispatching right now (skip this run). Never blocks, and the
        lock is always released when the block exits. In-process stores (one worker)
        always yield True."""
        ...


class DuplicateNameError(ValueError):
    """A customer or group name is already taken (names are unique per scope)."""


@runtime_checkable
class CatalogRepoPort(Protocol):
    """Persistence for customers and monitor groups.

    Deleting a customer deletes its groups; deleting a group ungroups its SSL domains
    and website monitors (they are never deleted)."""

    def list_customers(self) -> list[Customer]:
        """Ordered by name."""
        ...

    def get_customer(self, customer_id: int) -> Customer | None:
        ...

    def add_customer(self, name: str, contact_email: str | None = None) -> Customer:
        """Raises DuplicateNameError if the name exists."""
        ...

    def update_customer(self, customer_id: int, values: Mapping[str, Any]) -> Customer | None:
        """``values`` may hold ``name`` / ``contact_email``. Raises DuplicateNameError."""
        ...

    def delete_customer(self, customer_id: int) -> bool:
        ...

    def list_groups(self, customer_id: int | None = None) -> list[MonitorGroup]:
        """Ordered by name; only one customer's groups when ``customer_id`` is given."""
        ...

    def get_group(self, group_id: int) -> MonitorGroup | None:
        ...

    def add_group(self, customer_id: int, name: str) -> MonitorGroup:
        """Raises DuplicateNameError if the customer already has a group with that name."""
        ...

    def rename_group(self, group_id: int, name: str) -> MonitorGroup | None:
        """Raises DuplicateNameError."""
        ...

    def delete_group(self, group_id: int) -> bool:
        ...

    def list_alert_rules(
        self, *, customer_id: int | None = None, monitor_id: int | None = None
    ) -> list[AlertRule]:
        """Rules for exactly that scope (both None = global rules), ordered by target."""
        ...

    def add_alert_rule(self, rule: AlertRule) -> AlertRule:
        """Insert (``rule.id`` ignored). Exactly one of customer_id / monitor_id, or
        neither (global). Raises DuplicateNameError if the same target already exists
        for that scope."""
        ...

    def update_alert_rule(self, rule_id: int, values: Mapping[str, Any]) -> AlertRule | None:
        """``values`` may hold on_down / on_recovery / enabled. Unknown field → ValueError."""
        ...

    def delete_alert_rule(self, rule_id: int) -> bool:
        ...

    def recipients_for(self, monitor: Monitor, kind: AlertKind) -> list[str]:
        """Enabled EMAIL rule targets that apply to ``monitor`` for ``kind``: global +
        its customer's + its own; DOWN/REMINDER need on_down, RECOVERED needs
        on_recovery, TEST takes all. Lower-cased, de-duplicated, sorted."""
        ...


@runtime_checkable
class SettingsStorePort(Protocol):
    """Key/value settings saved from the admin UI (values are JSON-serialisable)."""

    def load(self) -> StoredSettings:
        """All saved values, plus the most recent updated_at / updated_by."""
        ...

    def save(self, changes: Mapping[str, Any], *, updated_by: str, now: datetime) -> None:
        """Upsert each key; a value of None DELETES the key (falls back to env).
        All changes in one transaction."""
        ...
