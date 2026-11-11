"""Per-key cooldown ("wait N seconds between two of these"), held in this process only."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Hashable


class Cooldown:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._seconds = seconds
        self._clock = clock
        self._started_at: dict[Hashable, float] = {}

    def remaining(self, key: Hashable) -> int:
        """Whole seconds (rounded up) before ``key`` may act again; 0 if it may now."""
        started_at = self._started_at.get(key)
        if started_at is None:
            return 0
        return max(0, math.ceil(started_at + self._seconds - self._clock()))

    def start(self, key: Hashable) -> None:
        self._started_at[key] = self._clock()
