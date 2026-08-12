"""User store backed by a JSON file (username + password hash + role).

Simple local auth: the file (``auth_users.json``, git-ignored) holds hashed
passwords only. A default admin is seeded on first run so you can log in.
"""

from __future__ import annotations

import json
import logging
import os

from .models import User
from .security import hash_password, verify_password

logger = logging.getLogger("ssl_monitor.auth")


class UserStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._users: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("users", []):
            self._users[entry["username"]] = entry

    def _save(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"users": list(self._users.values())}, fh, indent=2)

    @staticmethod
    def _to_user(entry: dict) -> User:
        return User(username=entry["username"], role=entry.get("role", "user"))

    def is_empty(self) -> bool:
        return not self._users

    def verify(self, username: str, password: str) -> User | None:
        entry = self._users.get(username)
        if not entry or "password_hash" not in entry:
            return None
        if verify_password(password, entry["password_hash"]):
            return self._to_user(entry)
        return None

    def get(self, username: str) -> User | None:
        entry = self._users.get(username)
        return self._to_user(entry) if entry else None

    def add(self, username: str, password: str, *, role: str = "user") -> User:
        entry = {"username": username, "role": role, "password_hash": hash_password(password)}
        self._users[username] = entry
        self._save()
        return self._to_user(entry)

    def set_password(self, username: str, new_password: str) -> bool:
        entry = self._users.get(username)
        if entry is None:
            return False
        entry["password_hash"] = hash_password(new_password)
        self._save()
        return True

    def delete(self, username: str) -> bool:
        existed = username in self._users
        self._users.pop(username, None)
        if existed:
            self._save()
        return existed

    def list(self) -> list[User]:
        return [self._to_user(e) for e in self._users.values()]

    def seed_default_admin(self, password: str) -> None:
        if self.is_empty():
            self.add("admin", password, role="admin")
            logger.warning(
                "No users found — seeded default admin 'admin'. Change the password."
            )
