"""Pure status classification and the days-remaining calculation.

No clock is read inside these functions — the caller supplies ``now`` (and, for
classification, the already-computed ``days_remaining``). This keeps every test
deterministic across machines and timezones.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .models import Status


def days_remaining(not_after: datetime, now: datetime) -> int:
    """Whole days from ``now`` until ``not_after`` (both UTC-aware).

    Uses ``timedelta.days``, which floors toward negative infinity: a cert that
    expired half a day ago yields -1 (→ expired), while one expiring in 12 hours
    yields 0 (→ still a warning, not yet expired).
    """
    if not_after.tzinfo is None or now.tzinfo is None:
        raise ValueError("days_remaining requires timezone-aware datetimes")
    return (not_after - now).days


def classify_status(
    reachable: bool,
    days_left: int | None,
    thresholds: Sequence[int],
) -> Status:
    """Map a probe outcome to a :class:`Status`.

    - probe failed (or no expiry parsed) → UNREACHABLE  (never a false "expiring")
    - days_left < 0                       → EXPIRED
    - 0 <= days_left <= max(thresholds)   → WARNING
    - days_left > max(thresholds)         → OK
    """
    if not reachable or days_left is None:
        return Status.UNREACHABLE
    if days_left < 0:
        return Status.EXPIRED
    if days_left <= max(thresholds):
        return Status.WARNING
    return Status.OK
