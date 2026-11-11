"""PostgreSQL engine (SQLAlchemy Core over the psycopg 3 driver).

One pooled engine per URL per process. Adapters borrow connections from it with
``engine.begin()`` so every unit of work is a single transaction.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

CONNECT_TIMEOUT_SECONDS = 5
_DRIVER_PREFIX = "postgresql+psycopg://"
_PLAIN_PREFIXES = ("postgresql://", "postgres://")


def normalize_database_url(url: str) -> str:
    """Accept the common ``postgresql://`` form and pin the psycopg 3 driver."""
    for prefix in _PLAIN_PREFIXES:
        if url.startswith(prefix):
            return _DRIVER_PREFIX + url[len(prefix):]
    return url


@lru_cache(maxsize=4)
def get_engine(database_url: str) -> Engine:
    return create_engine(
        normalize_database_url(database_url),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
