"""In-memory login rate limiter — brute-force protection for /auth/login.

Single-process (fine for the single-server deployment). Counts failed attempts
per key (username) within a sliding window; once the cap is hit, further
attempts are rejected until the oldest failure ages out of the window.
"""

from __future__ import annotations

import time


class LoginLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: int = 300) -> None:
        self._fails: dict[str, list[float]] = {}
        self.max_failures = max_failures
        self.window = window_seconds

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._fails.get(key, []) if t > now - self.window]
        if times:
            self._fails[key] = times
        else:
            self._fails.pop(key, None)
        return times

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait before another attempt, or 0 if allowed."""
        now = time.time()
        times = self._recent(key, now)
        if len(times) >= self.max_failures:
            return max(1, int(times[0] + self.window - now))
        return 0

    def record_failure(self, key: str) -> None:
        now = time.time()
        self._fails.setdefault(key, []).append(now)
        self._recent(key, now)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
