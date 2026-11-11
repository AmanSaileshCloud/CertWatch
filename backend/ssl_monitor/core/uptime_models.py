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
    """Outcome of one check of one monitor."""

    monitor_id: int
    checked_at: datetime
    is_up: bool
    status_code: int | None = None
    response_ms: int | None = None
    error: str | None = None
    region: str = DEFAULT_REGION
