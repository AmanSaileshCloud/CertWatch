"""Address rules for the SSRF guard (core.net_policy) — pure, no network."""

from __future__ import annotations

import ipaddress

import pytest

from backend.ssl_monitor.core.net_policy import (
    is_blocked_address,
    parse_ip_literal,
    strip_trailing_dot,
)


@pytest.mark.parametrize("host, expected", [
    ("2130706433", "127.0.0.1"),
    ("0x7f.1", "127.0.0.1"),
    ("0X7F.0.0.1", "127.0.0.1"),
    ("0177.0.0.1", "127.0.0.1"),
    ("127.1", "127.0.0.1"),
    ("0", "0.0.0.0"),
    ("169.254.169.254.", "169.254.169.254"),
    ("93.184.215.14", "93.184.215.14"),
    ("::1", "::1"),
    ("fe80::1%eth0", "fe80::1"),
])
def test_parse_ip_literal_recognises_all_numeric_forms(host, expected):
    assert parse_ip_literal(host) == ipaddress.ip_address(expected)


@pytest.mark.parametrize("host", ["example.com", "example.com.", "deadbeef.example", "1.2.3.4.5",
                                  "0x", "08", "cafe"])
def test_hostnames_are_not_ip_literals(host):
    assert parse_ip_literal(host) is None


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.1", "169.254.169.254", "0.0.0.0",
    "100.64.0.1", "192.0.0.170", "198.18.0.1", "240.0.0.1", "224.0.0.1",
    "::", "::1", "fc00::1", "fe80::1", "ff02::1",
    "::ffff:127.0.0.1", "::127.0.0.1", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::",
])
def test_internal_addresses_are_blocked(address):
    assert is_blocked_address(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", [
    "93.184.215.14", "8.8.8.8", "2606:2800:21f::1", "::ffff:93.184.215.14",
    "64:ff9b::808:808", "2002:808:808::",
])
def test_public_addresses_are_allowed(address):
    assert not is_blocked_address(ipaddress.ip_address(address))


def test_strip_trailing_dot():
    assert strip_trailing_dot("example.com.") == "example.com"
    assert strip_trailing_dot("example.com") == "example.com"
