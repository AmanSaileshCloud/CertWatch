"""Symmetric encryption for secrets saved from the admin UI (the SES SMTP password).

Uses Fernet (AES + HMAC) with ``SETTINGS_ENCRYPTION_KEY`` from the environment, so the
database alone never reveals a secret. The key itself is never logged.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from ..config.settings import Settings

logger = logging.getLogger("ssl_monitor.secrets")


class SecretBoxError(Exception):
    """The key is invalid, or a token cannot be decrypted with it."""


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError):
            raise SecretBoxError("not a valid Fernet key") from None

    @classmethod
    def from_settings(cls, settings: Settings) -> SecretBox | None:
        """The box for ``SETTINGS_ENCRYPTION_KEY``, or None (warned once) when it is
        missing or invalid."""
        return _box_for_key(settings.settings_encryption_key)

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            raise SecretBoxError("cannot decrypt — was SETTINGS_ENCRYPTION_KEY changed?") from None


@lru_cache(maxsize=4)
def _box_for_key(key: str | None) -> SecretBox | None:
    if not key:
        logger.warning("SETTINGS_ENCRYPTION_KEY is not set: SMTP credentials cannot be "
                       "saved from the Settings page")
        return None
    try:
        return SecretBox(key)
    except SecretBoxError:
        logger.warning("SETTINGS_ENCRYPTION_KEY is not a valid Fernet key: SMTP "
                       "credentials cannot be saved or read")
        return None
