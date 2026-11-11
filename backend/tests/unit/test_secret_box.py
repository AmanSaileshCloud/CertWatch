"""SecretBox: Fernet encryption of secrets saved from the Settings page."""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from backend.ssl_monitor.adapters.secret_box import SecretBox, SecretBoxError, _box_for_key
from backend.ssl_monitor.config.settings import Settings

KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def fresh_key_cache():
    _box_for_key.cache_clear()
    yield
    _box_for_key.cache_clear()


def test_round_trip_hides_the_plain_text():
    box = SecretBox(KEY)
    token = box.encrypt("smtp-pass")
    assert "smtp-pass" not in token
    assert box.decrypt(token) == "smtp-pass"


def test_wrong_key_cannot_decrypt():
    token = SecretBox(KEY).encrypt("smtp-pass")
    with pytest.raises(SecretBoxError):
        SecretBox(Fernet.generate_key().decode()).decrypt(token)


def test_garbage_token_cannot_decrypt():
    with pytest.raises(SecretBoxError):
        SecretBox(KEY).decrypt("not-a-token")


def test_invalid_key_is_rejected():
    with pytest.raises(SecretBoxError):
        SecretBox("too-short")


def test_from_settings_with_a_valid_key():
    box = SecretBox.from_settings(Settings.from_env(env={"SETTINGS_ENCRYPTION_KEY": KEY}))
    assert box is not None and box.decrypt(box.encrypt("x")) == "x"


@pytest.mark.parametrize("env", [{}, {"SETTINGS_ENCRYPTION_KEY": "not-a-fernet-key"}])
def test_from_settings_is_none_and_warns_once_without_a_usable_key(env, caplog):
    settings = Settings.from_env(env=env)
    with caplog.at_level(logging.WARNING, logger="ssl_monitor.secrets"):
        assert SecretBox.from_settings(settings) is None
        assert SecretBox.from_settings(settings) is None
    assert len(caplog.records) == 1
    assert "SETTINGS_ENCRYPTION_KEY" in caplog.text
    assert "not-a-fernet-key" not in caplog.text
