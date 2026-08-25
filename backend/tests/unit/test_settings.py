"""Config-layer tests: env parsing and local/aws boto kwargs switching."""

from __future__ import annotations

import os

import pytest

from backend.ssl_monitor.config.settings import (
    INSECURE_AUTH_SECRET,
    Settings,
    _load_dotenv,
)

GOOD_SECRET = "b7c9f2a1e4d8360b5a2f9c7e1d4b8a6035f2c9e7d1b4a8f60c3e9d2b7a154f8c6"


def test_defaults_are_local():
    s = Settings.from_env(env={})
    assert s.backend == "local"
    assert s.is_local is True
    assert s.notifier == "console"
    assert s.threshold_days == [30, 14, 7, 1]
    assert s.tls_timeout_seconds == 5.0


def test_threshold_parsing_dedupes_and_sorts_desc():
    s = Settings.from_env(env={"THRESHOLD_DAYS": "7, 30, 7, 1,14"})
    assert s.threshold_days == [30, 14, 7, 1]


def test_empty_thresholds_rejected():
    with pytest.raises(ValueError):
        Settings.from_env(env={"THRESHOLD_DAYS": " , "})


def test_local_boto_kwargs_include_endpoint_and_dummy_creds():
    s = Settings.from_env(env={"AWS_ENDPOINT_URL": "http://localhost:4566"})
    kwargs = s.boto_kwargs()
    assert kwargs["endpoint_url"] == "http://localhost:4566"
    assert kwargs["aws_access_key_id"]  # dummy creds injected
    assert kwargs["region_name"] == "us-east-1"


def test_aws_boto_kwargs_omit_endpoint():
    s = Settings.from_env(env={"BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    kwargs = s.boto_kwargs()
    assert "endpoint_url" not in kwargs
    assert s.is_local is False


def test_load_dotenv_sets_defaults_without_overriding(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\nSTORAGE=memory\nNOTIFIER=console\nALREADY_SET=fromfile\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STORAGE", raising=False)
    monkeypatch.setenv("ALREADY_SET", "fromenv")  # real env must win

    _load_dotenv(str(env_file))

    assert os.environ["STORAGE"] == "memory"        # filled from file
    assert os.environ["ALREADY_SET"] == "fromenv"   # not overridden


def test_load_dotenv_missing_file_is_noop():
    _load_dotenv("definitely-not-a-real-file.env")  # must not raise


# ── AUTH_SECRET / bootstrap-password validation ─────────────────


def test_a_real_random_secret_is_accepted():
    s = Settings.from_env(env={"AUTH_SECRET": GOOD_SECRET})
    assert s.auth_secret_problem is None
    assert s.auth_secret_is_insecure is False


@pytest.mark.parametrize(
    "secret",
    [
        "",                                          # unset
        INSECURE_AUTH_SECRET,                        # built-in default
        "CHANGE_ME_LONG_RANDOM_32_CHARS_MINIMUM",    # the shipped env template
        "change-me-please-this-is-long-enough-ok",   # hyphenated placeholder
        "your-secret-key-goes-right-here-padded",    # another common template
        "short",                                     # under the length floor
        "a" * 31,                                    # one char under the floor
    ],
)
def test_placeholder_and_weak_secrets_are_rejected(secret):
    s = Settings.from_env(env={"AUTH_SECRET": secret})
    assert s.auth_secret_problem is not None
    assert s.auth_secret_is_insecure is True


def test_shipped_env_example_secret_is_rejected():
    """The exact value in deploy/certwatch.env.example must never boot.

    It is published in this repo, so a deployment still using it is signing
    JWTs with a key any reader already has.
    """
    s = Settings.from_env(
        env={"AUTH_SECRET": "CHANGE_ME_LONG_RANDOM_32_CHARS_MINIMUM"}
    )
    assert "placeholder" in s.auth_secret_problem


def test_bootstrap_password_placeholder_is_rejected():
    s = Settings.from_env(
        env={
            "AUTH_SECRET": GOOD_SECRET,
            "AUTH_BOOTSTRAP_PASSWORD": "CHANGE_ME_STRONG_PASSWORD",
        }
    )
    assert s.auth_secret_problem is None          # the secret itself is fine
    assert s.bootstrap_password_problem is not None


def test_real_bootstrap_password_is_accepted():
    s = Settings.from_env(
        env={"AUTH_SECRET": GOOD_SECRET, "AUTH_BOOTSTRAP_PASSWORD": "s3cur3-pw-9f2c"}
    )
    assert s.bootstrap_password_problem is None
