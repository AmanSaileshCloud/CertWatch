from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    username: str
    role: str = "user"  # "user" | "admin"
    email: str | None = None
    email_verified: bool = False
