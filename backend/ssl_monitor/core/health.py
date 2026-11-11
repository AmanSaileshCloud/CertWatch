"""Is CertWatch ready to do its job? Pure rules for /health/ready."""

from __future__ import annotations

from datetime import datetime, timedelta

UPTIME_WORKER = "uptime"

# The uptime worker beats after every finished cycle (every ~10 s). A cycle with
# many slow sites can take a minute or two, so allow some slack before alarming.
MAX_HEARTBEAT_AGE = timedelta(minutes=3)


def heartbeat_problem(
    last_beat: datetime | None, now: datetime, max_age: timedelta = MAX_HEARTBEAT_AGE
) -> str | None:
    """Why the uptime worker looks stopped, or None if it is beating."""
    if last_beat is None:
        return "the uptime worker has never reported in"
    age = now - last_beat
    if age > max_age:
        return f"the uptime worker last reported {int(age.total_seconds())} s ago"
    return None
