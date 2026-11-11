"""PostgreSQL fixtures for integration tests.

Tests marked ``postgres`` run against ``TEST_DATABASE_URL`` (env var, or the repo
``.env``) — a throwaway database that is wiped and re-migrated each session.
Without it they are skipped, so the default suite still needs no services.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from backend.ssl_monitor.adapters.db.engine import get_engine
from backend.ssl_monitor.adapters.db.tables import metadata

REPO_ROOT = Path(__file__).resolve().parents[3]


def _test_database_url() -> str | None:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "TEST_DATABASE_URL" and value.strip():
            return value.strip()
    return None


def _alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "backend" / "migrations"))
    config.attributes["database_url"] = url
    return config


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping PostgreSQL tests")
    return url


@pytest.fixture(scope="session")
def pg_alembic(pg_url: str) -> Config:
    """Alembic config for the test database (for tests that migrate up and down)."""
    return _alembic_config(pg_url)


@pytest.fixture(scope="session")
def pg_engine(pg_url: str, pg_alembic: Config) -> Engine:
    engine = get_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(pg_alembic, "head")
    return engine


@pytest.fixture
def pg(pg_engine: Engine) -> Engine:
    """A migrated, empty database for one test."""
    names = ", ".join(t.name for t in metadata.sorted_tables)
    with pg_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    return pg_engine
