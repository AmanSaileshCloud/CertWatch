"""Dependency wiring — the single seam where Settings selects concrete adapters.

This is the only module that knows which storage/notifier implementation is
live. Handlers, the API, and the runner call these factories; tests bypass them
and inject fakes directly.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine

from ..adapters.db.engine import get_engine
from ..adapters.notifier.base import NotifierPort
from ..adapters.notifier.console import ConsoleNotifier
from ..adapters.notifier.sns_ses import SnsSesNotifier
from ..adapters.storage.base import StoragePort
from ..adapters.storage.memory import MemoryStorage
from ..adapters.storage.postgres import PostgresStorage
from ..adapters.storage.sqlite import SqliteStorage
from ..config.settings import Settings

# Process-global in-memory store, used only when STORAGE=memory. It persists for
# the life of the API process (fine for no-infra local dev / the frontend demo),
# but NOT across separate processes — the `--once` runner won't see it.
_MEMORY_STORE: MemoryStorage | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide Settings, loaded once from the environment."""
    return Settings.from_env()


def make_engine(settings: Settings | None = None) -> Engine:
    """The shared PostgreSQL engine. Fails loudly if DATABASE_URL is missing."""
    settings = settings or get_settings()
    if not settings.database_url:
        raise RuntimeError("STORAGE=postgres requires DATABASE_URL to be set.")
    return get_engine(settings.database_url)


def make_storage(settings: Settings | None = None) -> StoragePort:
    """Select storage by config.

    ``postgres`` (default) → PostgreSQL at DATABASE_URL.
    ``sqlite``             → legacy single-file DB (pre-Postgres deployments).
    ``memory``             → process-global in-memory store (dev / tests).
    """
    settings = settings or get_settings()
    if settings.storage == "memory":
        global _MEMORY_STORE
        if _MEMORY_STORE is None:
            _MEMORY_STORE = MemoryStorage()
        return _MEMORY_STORE
    if settings.storage == "sqlite":
        return SqliteStorage(settings.sqlite_path)
    return PostgresStorage(make_engine(settings))


def make_notifier(settings: Settings | None = None) -> NotifierPort:
    """Select the notifier by config: ``console`` (default) or ``sns_ses``."""
    settings = settings or get_settings()
    if settings.notifier == "sns_ses":
        return SnsSesNotifier(settings)
    return ConsoleNotifier()
