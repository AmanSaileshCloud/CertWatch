"""Config-layer tests: env parsing and local/aws boto kwargs switching."""

from __future__ import annotations

import os

import pytest

from backend.ssl_monitor.config.settings import Settings, _load_dotenv


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
