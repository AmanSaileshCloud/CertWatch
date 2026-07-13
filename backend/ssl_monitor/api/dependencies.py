"""FastAPI dependency providers.

Auth is self-contained JWT: `get_current_user` decodes a bearer token signed
with ``AUTH_SECRET``. Tests override these via ``app.dependency_overrides``.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from ..adapters.notifier.base import NotifierPort
from ..adapters.storage.base import StoragePort
from ..auth.models import User
from ..auth.rate_limit import LoginLimiter
from ..auth.security import decode_access_token
from ..auth.users import UserStore
from ..config.settings import Settings
from ..core.cert_probe import probe_cert, probe_cert_info
from ..handlers._deps import get_settings, make_notifier, make_storage
from ..services.checker import ProbeFn

logger = logging.getLogger("ssl_monitor.auth")

# When AUTH_ENABLED=false (local dev / tests) every request is an admin.
_ANONYMOUS = User(username="anonymous", role="admin")


def get_app_settings() -> Settings:
    return get_settings()


@lru_cache(maxsize=1)
def get_storage() -> StoragePort:
    """Process-wide storage. Cached so the adapter (and its schema init /
    connection setup) is built once, not rebuilt on every request."""
    return make_storage()


def get_notifier() -> NotifierPort:
    return make_notifier()


def get_probe() -> ProbeFn:
    """The probe function used by POST /checks/run (overridable in tests)."""
    return probe_cert


def get_cert_prober():
    """Full-cert-details prober for the on-demand detail endpoint (overridable)."""
    return probe_cert_info


@lru_cache(maxsize=1)
def get_login_limiter() -> LoginLimiter:
    """Process-wide login rate limiter."""
    return LoginLimiter()


@lru_cache(maxsize=1)
def get_user_store() -> UserStore:
    """Process-wide user store. Seeds a default admin on first run if empty."""
    settings = get_settings()
    store = UserStore(settings.users_file)
    if settings.auth_enabled and store.is_empty():
        bootstrap = os.environ.get("AUTH_BOOTSTRAP_PASSWORD", "admin123")
        store.seed_default_admin(bootstrap)
    return store


def get_current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Validate the Bearer JWT and return the user.

    With auth disabled (``AUTH_ENABLED=false``) every request is an anonymous
    admin — handy for local development and the test suite.
    """
    if not settings.auth_enabled:
        return _ANONYMOUS

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token, settings.auth_secret)
    except Exception as exc:  # jwt.PyJWTError and friends
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return User(username=claims.get("sub", ""), role=claims.get("role", "user"))


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Like get_current_user, but rejects non-admins with 403."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin privilege required")
    return user
