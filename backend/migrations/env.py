"""Alembic environment: runs migrations against ``DATABASE_URL``."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from backend.ssl_monitor.adapters.db.engine import get_engine
from backend.ssl_monitor.adapters.db.tables import metadata
from backend.ssl_monitor.config.settings import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    url = config.attributes.get("database_url") or Settings.from_env().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set — cannot run migrations.")
    return url


def run_migrations_online() -> None:
    with get_engine(_database_url()).connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
