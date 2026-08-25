"""Tests for login rate-limiting and the change-password endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.ssl_monitor.adapters.storage.memory import MemoryStorage
from backend.ssl_monitor.api.app import app
from backend.ssl_monitor.api.dependencies import (
    get_app_settings,
    get_current_user,
    get_login_limiter,
    get_storage,
    get_user_store,
)
from backend.ssl_monitor.auth.models import User
from backend.ssl_monitor.auth.rate_limit import LoginLimiter
from backend.ssl_monitor.auth.users import UserStore
from backend.ssl_monitor.config.settings import Settings

SECRET = "test-secret-key-long-enough-1234567890"


def test_limiter_locks_after_max_failures():
    lim = LoginLimiter(max_failures=3, window_seconds=300)
    assert lim.retry_after("bob") == 0
    for _ in range(3):
        lim.record_failure("bob")
    assert lim.retry_after("bob") > 0        # locked
    lim.reset("bob")
    assert lim.retry_after("bob") == 0       # cleared on success


@pytest.fixture
def ctx(tmp_path):
    settings = Settings.from_env(env={"AUTH_ENABLED": "true", "AUTH_SECRET": SECRET})
    store = UserStore(str(tmp_path / "users.json"))
    store.add("admin", "admin123", role="admin")
    limiter = LoginLimiter(max_failures=3, window_seconds=300)

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    app.dependency_overrides[get_storage] = lambda: MemoryStorage()
    client = TestClient(app)
    yield client, store
    app.dependency_overrides.clear()


def test_login_locks_out_after_repeated_failures(ctx):
    client, _ = ctx
    for _ in range(3):
        assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    # locked now — even the correct password is rejected with 429
    r = client.post("/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_successful_login_resets_the_limiter(ctx):
    client, _ = ctx
    client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert client.post("/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200


# ── startup guard: refuse to serve with an unusable signing key ──


def _settings(**overrides) -> Settings:
    env = {"AUTH_ENABLED": "true", "AUTH_SECRET": SECRET}
    env.update(overrides)
    return Settings.from_env(env=env)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        # The exact value shipped in deploy/certwatch.env.example.
        ({"AUTH_SECRET": "CHANGE_ME_LONG_RANDOM_32_CHARS_MINIMUM"}, "placeholder"),
        ({"AUTH_SECRET": "too-short"}, "characters"),
        ({"AUTH_SECRET": ""}, "empty"),
        ({"AUTH_BOOTSTRAP_PASSWORD": "CHANGE_ME_STRONG_PASSWORD"}, "placeholder"),
    ],
)
def test_startup_refuses_insecure_auth_config(monkeypatch, overrides, expected):
    """A copied-but-unedited env file must crash the API, not serve it.

    The placeholders are public, so booting with them means anyone can forge an
    admin token. Entering the TestClient context runs the real lifespan.
    """
    bad = _settings(**overrides)
    monkeypatch.setattr("backend.ssl_monitor.api.app.get_app_settings", lambda: bad)
    with pytest.raises(RuntimeError, match=expected):
        with TestClient(app):
            pass


def test_startup_succeeds_with_a_real_secret(monkeypatch):
    good = _settings(AUTH_BOOTSTRAP_PASSWORD="a-real-generated-password")
    monkeypatch.setattr("backend.ssl_monitor.api.app.get_app_settings", lambda: good)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_startup_guard_is_skipped_when_auth_disabled(monkeypatch):
    """AUTH_ENABLED=false is the documented local-dev mode — don't block it."""
    off = Settings.from_env(env={"AUTH_ENABLED": "false"})
    monkeypatch.setattr("backend.ssl_monitor.api.app.get_app_settings", lambda: off)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_change_password_flow(ctx):
    client, store = ctx
    app.dependency_overrides[get_current_user] = lambda: User("admin", "admin")

    # wrong current password → 401
    assert client.post(
        "/auth/change-password", json={"current_password": "nope", "new_password": "newpass12"}
    ).status_code == 401
    # too short → 422
    assert client.post(
        "/auth/change-password", json={"current_password": "admin123", "new_password": "short"}
    ).status_code == 422
    # success → 200, and the password actually changes
    assert client.post(
        "/auth/change-password", json={"current_password": "admin123", "new_password": "newpass12"}
    ).status_code == 200
    assert store.verify("admin", "admin123") is None
    assert store.verify("admin", "newpass12") is not None
