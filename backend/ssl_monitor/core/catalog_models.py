"""Customers and monitor groups (Customer -> Group -> SSL domains + website monitors).

Pure data — no I/O. A group belongs to exactly one customer; each SSL domain and
each website monitor is in at most one group.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    contact_email: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class MonitorGroup:
    id: int
    customer_id: int
    name: str
    created_at: datetime | None = None
