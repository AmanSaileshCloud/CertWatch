"""Short-lived caches for the alert/email configuration.

Loading the configuration reads the app_settings table and decrypts the SMTP
password. The uptime worker needs it every tick (10 s) and the API on every
monitor list, so it is kept for ``ttl_seconds`` instead of re-read each time.
Changes saved on the Settings page therefore reach the worker and the other API
process within ``ttl_seconds``; the process that saved them drops its copy at once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

from ..core.app_settings_models import AlertConfig

DEFAULT_TTL_SECONDS = 30.0

T = TypeVar("T")


class AlertConfigCache:
    """The configuration from ``load()``, reloaded once it is ``ttl_seconds`` old,
    or when it is asked for from a different ``source`` (settings store).

    A failed load is not cached: the error reaches the caller and the next call
    tries again.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._value: AlertConfig | None = None
        self._source: object = None
        self._loaded_at = 0.0

    def get(self, load: Callable[[], AlertConfig], source: object = None) -> AlertConfig:
        with self._lock:
            now = self._clock()
            stale = now - self._loaded_at >= self._ttl
            if self._value is None or stale or source is not self._source:
                self._value = load()
                self._source = source
                self._loaded_at = now
            return self._value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None


class BuiltFor(Generic[T]):
    """Rebuilds a value (e.g. the email notifier and its AWS client) only when the
    configuration it was built from changes."""

    def __init__(self, build: Callable[[AlertConfig], T]) -> None:
        self._build = build
        self._config: AlertConfig | None = None
        self._value: T | None = None

    def get(self, config: AlertConfig) -> T:
        if self._value is None or config != self._config:
            self._value = self._build(config)
            self._config = config
        return self._value
