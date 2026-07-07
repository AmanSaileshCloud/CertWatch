"""In-memory storage adapter.

Used by unit tests and as a zero-dependency fallback. Not selected by config in
normal operation — SQLite is the real backend on the server — but it satisfies
:class:`StoragePort` for fast, network-free testing.
"""

from __future__ import annotations

from dataclasses import replace

from ...core.models import DomainRecord


class MemoryStorage:
    def __init__(self) -> None:
        self._items: dict[str, DomainRecord] = {}

    @staticmethod
    def _copy(record: DomainRecord) -> DomainRecord:
        # replace() is shallow — copy the mutable list field explicitly.
        return replace(record, notify_emails=list(record.notify_emails))

    def put(self, record: DomainRecord) -> None:
        # Copy so callers mutating their record don't change stored state.
        self._items[record.domain] = self._copy(record)

    def get(self, domain: str) -> DomainRecord | None:
        found = self._items.get(domain)
        return self._copy(found) if found is not None else None

    def list(self) -> list[DomainRecord]:
        return [self._copy(r) for r in self._items.values()]

    def delete(self, domain: str) -> None:
        self._items.pop(domain, None)
