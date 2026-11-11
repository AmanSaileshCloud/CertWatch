"""Dependency wiring — the single seam where Settings selects concrete adapters.

This is the only module that knows which storage/notifier implementation is
live. Handlers, the API, and the runner call these factories; tests bypass them
and inject fakes directly.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import Engine

from ..adapters.db.engine import get_engine
from ..adapters.notifier.base import NotifierPort
from ..adapters.notifier.console import ConsoleNotifier
from ..adapters.notifier.ses_smtp import SesSmtpNotifier
from ..adapters.notifier.sns_ses import SnsSesNotifier
from ..adapters.secret_box import SecretBox
from ..adapters.storage.base import (
    CatalogRepoPort,
    SettingsStorePort,
    StoragePort,
    UptimeRepoPort,
)
from ..adapters.storage.catalog_memory import MemoryCatalogRepo
from ..adapters.storage.catalog_postgres import PostgresCatalogRepo
from ..adapters.storage.memory import MemoryStorage
from ..adapters.storage.postgres import PostgresStorage
from ..adapters.storage.settings_memory import MemorySettingsStore
from ..adapters.storage.settings_postgres import PostgresSettingsStore
from ..adapters.storage.sqlite import SqliteStorage
from ..adapters.storage.uptime_memory import MemoryUptimeRepo
from ..adapters.storage.uptime_postgres import PostgresUptimeRepo
from ..config.settings import Settings
from ..core.app_settings_models import AlertConfig, EmailTransport
from ..services.app_settings import load_alert_config
from ..services.catalog import unassign_customer, ungroup_members
from ..services.uptime_alerts import catalog_names

# Process-global in-memory store, used only when STORAGE=memory. It persists for
# the life of the API process (fine for no-infra local dev / the frontend demo),
# but NOT across separate processes — the `--once` runner won't see it.
_MEMORY_STORE: MemoryStorage | None = None
_MEMORY_UPTIME_REPO: MemoryUptimeRepo | None = None
_MEMORY_CATALOG_REPO: MemoryCatalogRepo | None = None
_MEMORY_SETTINGS_STORE: MemorySettingsStore | None = None

logger = logging.getLogger("ssl_monitor.notifier")


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


def make_uptime_repo(settings: Settings | None = None) -> UptimeRepoPort:
    """Select the uptime repo by config.

    ``memory`` → process-global in-memory repo (dev / tests).
    Anything else → PostgreSQL; uptime has no SQLite implementation, so
    ``sqlite`` storage also uses DATABASE_URL (and fails loudly without it).
    """
    settings = settings or get_settings()
    if settings.storage == "memory":
        global _MEMORY_UPTIME_REPO
        if _MEMORY_UPTIME_REPO is None:
            _MEMORY_UPTIME_REPO = MemoryUptimeRepo(
                name_lookup=lambda monitor: catalog_names(make_catalog_repo(settings), monitor)
            )
        return _MEMORY_UPTIME_REPO
    return PostgresUptimeRepo(make_engine(settings))


def make_catalog_repo(settings: Settings | None = None) -> CatalogRepoPort:
    """Select the customers/groups repo by config (same rule as the uptime repo).

    In memory mode there are no foreign keys, so deleting a group ungroups the
    in-memory domains and monitors (``on_groups_deleted``), and deleting a customer
    clears their customer (``on_customer_deleted``).
    """
    settings = settings or get_settings()
    if settings.storage == "memory":
        global _MEMORY_CATALOG_REPO
        if _MEMORY_CATALOG_REPO is None:
            _MEMORY_CATALOG_REPO = MemoryCatalogRepo(
                on_groups_deleted=lambda ids: _ungroup_memory_members(settings, ids),
                on_customer_deleted=lambda cid: _unassign_memory_customer(settings, cid),
            )
        return _MEMORY_CATALOG_REPO
    return PostgresCatalogRepo(make_engine(settings))


def _ungroup_memory_members(settings: Settings, group_ids: set[int]) -> None:
    ungroup_members(
        make_storage(settings), make_uptime_repo(settings), group_ids, datetime.now(timezone.utc)
    )


def _unassign_memory_customer(settings: Settings, customer_id: int) -> None:
    unassign_customer(
        make_storage(settings), make_uptime_repo(settings), customer_id,
        datetime.now(timezone.utc),
    )


def make_settings_store(settings: Settings | None = None) -> SettingsStorePort:
    """Settings saved from the admin UI (same storage rule as the uptime repo)."""
    settings = settings or get_settings()
    if settings.storage == "memory":
        global _MEMORY_SETTINGS_STORE
        if _MEMORY_SETTINGS_STORE is None:
            _MEMORY_SETTINGS_STORE = MemorySettingsStore()
        return _MEMORY_SETTINGS_STORE
    return PostgresSettingsStore(make_engine(settings))


def make_secret_box(settings: Settings | None = None) -> SecretBox | None:
    """The SETTINGS_ENCRYPTION_KEY cipher, or None when the key is missing/invalid."""
    return SecretBox.from_settings(settings or get_settings())


def make_alert_config(settings: Settings | None = None) -> AlertConfig:
    """The effective alert/email configuration, read fresh on every call so changes
    saved on the Settings page apply without a restart."""
    settings = settings or get_settings()
    return load_alert_config(make_settings_store(settings), settings, make_secret_box(settings))


def make_email_notifier(config: AlertConfig, settings: Settings) -> NotifierPort:
    """The notifier for ``config``: NOTIFIER=console (local dev) always logs instead;
    otherwise SES over SMTP or the SES/SNS API with the instance role."""
    if settings.notifier != "sns_ses":
        return ConsoleNotifier()
    if config.transport is EmailTransport.SMTP:
        return SesSmtpNotifier(config)
    return SnsSesNotifier(replace(
        settings, alert_email=config.alert_email, alert_recipients=config.alert_recipients))


def delivery_method(config: AlertConfig, settings: Settings) -> str:
    """How ``make_email_notifier`` delivers, in words for the admin."""
    if settings.notifier != "sns_ses":
        return "the server log (NOTIFIER=console)"
    if config.transport is EmailTransport.SMTP:
        return "SES SMTP"
    return "SES (IAM role)"


def make_notifier(settings: Settings | None = None) -> NotifierPort:
    """The notifier for SSL and uptime alerts, honouring the Settings page.

    Falls back to the environment-only SNS/SES notifier when the saved settings
    cannot be read (e.g. the database is down or STORAGE=sqlite has no table)."""
    settings = settings or get_settings()
    if settings.notifier != "sns_ses":
        return ConsoleNotifier()
    try:
        config = make_alert_config(settings)
    except Exception as exc:  # noqa: BLE001 - alerts must still go out on env values
        logger.warning("Could not load saved alert settings, using environment values: %s", exc)
        return SnsSesNotifier(settings)
    return make_email_notifier(config, settings)
