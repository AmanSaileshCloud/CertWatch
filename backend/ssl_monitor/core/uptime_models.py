"""Uptime-monitoring domain models. Pure data — no I/O, no env reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

ALLOWED_INTERVAL_SECONDS = (60, 300, 600)
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60
DEFAULT_REGION = "default"


class MonitorType(str, Enum):
    """Kind of check. Phase 1 ships HTTP only; ping/TCP/DNS slot in here later."""

    HTTP = "http"


class MonitorState(str, Enum):
    """Confirmed health of a monitor (after the consecutive-failure rule)."""

    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"


class CloseReason(str, Enum):
    """Why an incident was closed."""

    RECOVERED = "recovered"  # the site came back (recovery threshold reached)
    PAUSED = "paused"  # the monitor was paused during the outage
    EDITED = "edited"  # the monitor's target changed (URL / expected status / keyword)


class AlertChannel(str, Enum):
    EMAIL = "email"
    SNS = "sns"


@dataclass(frozen=True)
class Monitor:
    """A website being watched, plus its last-known check state."""

    id: int
    name: str
    url: str
    interval_seconds: int
    timeout_seconds: int = 10
    expected_status: int = 200
    keyword: str | None = None
    customer_id: int | None = None
    group_id: int | None = None
    type: MonitorType = MonitorType.HTTP
    is_paused: bool = False
    state: MonitorState = MonitorState.UNKNOWN
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    first_failure_at: datetime | None = None
    next_check_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_status_code: int | None = None
    last_response_ms: int | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check of one monitor.

    ``monitor_version`` is the checked monitor's ``updated_at`` when it was claimed;
    a result whose version no longer matches the stored monitor is stale (the
    monitor was edited mid-check) and is not saved. None means "don't compare"."""

    monitor_id: int
    checked_at: datetime
    is_up: bool
    status_code: int | None = None
    response_ms: int | None = None
    error: str | None = None
    region: str = DEFAULT_REGION
    monitor_version: datetime | None = None


class IncidentEvent(str, Enum):
    """What one check result did to a monitor's confirmed state."""

    NONE = "none"
    WENT_DOWN = "went_down"  # failure threshold reached: open an incident
    RECOVERED = "recovered"  # recovery threshold reached: close the incident


@dataclass(frozen=True)
class MonitorTransition:
    """A check result plus the monitor state it leads to (output of the incident rules)."""

    result: CheckResult
    state: MonitorState
    consecutive_failures: int
    consecutive_successes: int
    first_failure_at: datetime | None
    event: IncidentEvent = IncidentEvent.NONE

    @property
    def monitor_id(self) -> int:
        return self.result.monitor_id


@dataclass(frozen=True)
class Incident:
    """One confirmed outage of one monitor. Open while ``resolved_at`` is None."""

    id: int
    monitor_id: int
    started_at: datetime
    resolved_at: datetime | None = None
    duration_seconds: int | None = None
    reason: str | None = None
    down_alert_sent_at: datetime | None = None
    recovery_alert_sent_at: datetime | None = None
    close_reason: CloseReason | None = None

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
