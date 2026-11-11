"""Database URL handling and storage selection (no database needed)."""

from __future__ import annotations

import pytest

from backend.ssl_monitor.adapters.db.engine import normalize_database_url
from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.handlers._deps import make_storage


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_url_is_pinned_to_psycopg_driver(given, expected):
    assert normalize_database_url(given) == expected


def test_postgres_is_the_default_storage():
    assert Settings.from_env(env={}).storage == "postgres"


def test_postgres_without_database_url_fails_loudly():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        make_storage(Settings.from_env(env={}))


def test_memory_storage_still_selectable():
    assert isinstance(make_storage(Settings.from_env(env={"STORAGE": "memory"})), MemoryStorage)
