"""PostgreSQL settings store (implements :class:`SettingsStorePort`): the ``app_settings``
key/value table, values as JSONB."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert

from ...core.app_settings_models import StoredSettings
from ..db.tables import app_settings


class PostgresSettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self) -> StoredSettings:
        with self._engine.connect() as conn:
            rows = conn.execute(select(app_settings)).all()
        values = {row.key: row.value for row in rows}
        latest = max(rows, key=lambda row: row.updated_at, default=None)
        if latest is None:
            return StoredSettings(values=values)
        return StoredSettings(values=values, updated_at=latest.updated_at,
                              updated_by=latest.updated_by)

    def save(self, changes: Mapping[str, Any], *, updated_by: str, now: datetime) -> None:
        with self._engine.begin() as conn:
            for key, value in changes.items():
                if value is None:
                    conn.execute(delete(app_settings).where(app_settings.c.key == key))
                else:
                    conn.execute(_upsert(key, value, updated_by, now))


def _upsert(key: str, value: Any, updated_by: str, now: datetime) -> Any:
    statement = insert(app_settings).values(
        key=key, value=value, updated_at=now, updated_by=updated_by)
    return statement.on_conflict_do_update(
        index_elements=[app_settings.c.key],
        set_={"value": statement.excluded.value, "updated_at": now, "updated_by": updated_by},
    )
