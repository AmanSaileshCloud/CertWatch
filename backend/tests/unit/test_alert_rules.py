"""Alert-rule validation: the email check shared with the settings service."""

from __future__ import annotations

import pytest

from backend.ssl_monitor.services.alert_rules import is_valid_email


@pytest.mark.parametrize("address", [
    "ops@example.com",
    "first.last+alerts@example.com",
    "team@mail.eu.example.co.uk",
    "a_b-c@my-host.io",
    "x" * 64 + "@example.com",
])
def test_valid_addresses(address):
    assert is_valid_email(address)


@pytest.mark.parametrize("address", [
    "a,b@x.com",
    "<x>@y.z",
    "<x>@y.com",
    "a b@x.com",
    "a;b@x.com",
    '"a"@x.com',
    "x@y",
    "x@y.c",
    "x@y.123",
    "x@-y.com",
    "x@y_z.com",
    "a@b@c.com",
    "@x.com",
    "x@",
    "ops@example.com\n",
    "ops\x00@example.com",
    "x" * 65 + "@example.com",
    "x@" + "a" * 250 + ".com",
    "",
    None,
    42,
])
def test_invalid_addresses(address):
    assert not is_valid_email(address)
