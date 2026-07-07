"""Storage port — the interface the API, runner, and handlers depend on.

Defined as a ``Protocol`` so any implementation (SQLite, in-memory fake) is
accepted without inheritance. Core never imports this; only adapters and the
wiring layer do.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...core.models import DomainRecord


@runtime_checkable
class StoragePort(Protocol):
    def put(self, record: DomainRecord) -> None:
        """Insert or overwrite a domain record by its ``domain`` key."""
        ...

    def get(self, domain: str) -> DomainRecord | None:
        """Return the record for ``domain`` or None if absent."""
        ...

    def list(self) -> list[DomainRecord]:
        """Return all records (table scan; fine for the dashboard's scale)."""
        ...

    def delete(self, domain: str) -> None:
        """Delete a record by key; a no-op if it does not exist."""
        ...
