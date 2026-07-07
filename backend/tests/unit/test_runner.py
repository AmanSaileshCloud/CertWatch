"""Runner CLI unit tests (pure parts only — no network)."""

from __future__ import annotations

import pytest

from backend.runner.run_checker import _parse_target


@pytest.mark.parametrize(
    "value, expected",
    [
        ("google.com", ("google.com", 443)),
        ("example.com:8443", ("example.com", 8443)),
        ("10.0.0.1:443", ("10.0.0.1", 443)),
    ],
)
def test_parse_target(value, expected):
    assert _parse_target(value) == expected
