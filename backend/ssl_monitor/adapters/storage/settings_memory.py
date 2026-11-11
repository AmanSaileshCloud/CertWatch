"""In-memory settings store (implements :class:`SettingsStorePort`) for dev and tests.

Values go through a JSON round trip, so they come back exactly as from Postgres JSONB
(tuples become lists, and so on).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ...core.app_settings_models import StoredSettings


@dataclass(frozen=True)
class _Row:
    value: Any
    updated_at: datetime
    updated_by: str


class MemorySettingsStore:
    def __init__(self) -> None:
        self._rows: dict[str, _Row] = {}

    def load(self) -> StoredSettings:
        values = {key: json.loads(row.value) for key, row in self._rows.items()}
        latest = max(self._rows.values(), key=lambda row: row.updated_at, default=None)
        if latest is None:
            return StoredSettings(values=values)
        return StoredSettings(values=values, updated_at=latest.updated_at,
                              updated_by=latest.updated_by)

    def save(self, changes: Mapping[str, Any], *, updated_by: str, now: datetime) -> None:
        rows = dict(self._rows)
        for key, value in changes.items():
            if value is None:
                rows.pop(key, None)
            else:
                rows[key] = _Row(json.dumps(value), now, updated_by)
        self._rows = rows  # all or nothing: json.dumps failing leaves the old state
