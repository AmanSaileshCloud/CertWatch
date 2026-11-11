"""Monitor-URL safety rules (SSRF guard) — fake resolver, no real DNS."""

from __future__ import annotations

import socket

import pytest

from backend.ssl_monitor.services.errors import BlockedTarget, InvalidMonitor
from backend.ssl_monitor.services.url_policy import validate_monitor_url


def resolver(mapping: dict[str, list[str]]):
    def resolve(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, 0)) for ip in mapping[host]]
    return resolve


PUBLIC = resolver({"example.org": ["93.184.215.14"], "v6.example.org": ["2606:2800:21f::1"]})


def check(url: str, *, allow_private: bool = False, resolve=PUBLIC) -> str:
    return validate_monitor_url(url, allow_private=allow_private, resolve=resolve)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "172.16.5.4", "172.31.255.255", "192.168.1.1",
    "169.254.169.254", "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fc00::1", "fd12::1",
    "fe80::1", "::ffff:10.0.0.1", "::",
])
def test_internal_ip_literals_are_blocked(address):
    host = f"[{address}]" if ":" in address else address
    with pytest.raises(BlockedTarget) as exc:
        check(f"http://{host}/")
    assert address in str(exc.value) or address.startswith("::ffff")
    assert "UPTIME_ALLOW_PRIVATE_TARGETS=true" in str(exc.value)


@pytest.mark.parametrize("url", [
    "http://2130706433/", "http://0x7f.1/", "http://0177.0.0.1/", "http://127.1/", "http://0/",
    "http://169.254.169.254./", "http://100.64.0.1/", "http://[::127.0.0.1]/",
    "http://[64:ff9b::a9fe:a9fe]/", "http://[2002:a9fe:a9fe::]/",
])
def test_legacy_and_embedded_ip_forms_are_blocked(url):
    with pytest.raises(BlockedTarget):
        check(url)
    assert check(url, allow_private=True) == url


def test_trailing_dot_hostname_is_resolved_without_the_dot():
    with pytest.raises(BlockedTarget, match="127.0.0.1"):
        check("http://localhost./", resolve=resolver({"localhost": ["127.0.0.1"]}))


def test_metadata_address_error_message():
    with pytest.raises(BlockedTarget) as exc:
        check("http://169.254.169.254/latest/meta-data")
    assert str(exc.value) == (
        "URL points to a private or internal address (169.254.169.254); set "
        "UPTIME_ALLOW_PRIVATE_TARGETS=true to allow internal sites."
    )


def test_hostname_resolving_to_loopback_is_blocked():
    with pytest.raises(BlockedTarget, match="127.0.0.1"):
        check("http://localhost:8080/", resolve=resolver({"localhost": ["127.0.0.1"]}))


def test_hostname_with_any_internal_address_is_blocked():
    mixed = resolver({"mixed.example": ["93.184.215.14", "10.1.2.3"]})
    with pytest.raises(BlockedTarget, match="10.1.2.3"):
        check("https://mixed.example", resolve=mixed)


def test_hostname_resolving_to_mapped_private_ipv6_is_blocked():
    with pytest.raises(BlockedTarget):
        check("https://sneaky.example", resolve=resolver({"sneaky.example": ["::ffff:192.168.0.1"]}))


@pytest.mark.parametrize("url", [
    "https://example.org", "http://example.org:8080/health?x=1", "https://v6.example.org/",
    "https://93.184.215.14/", "https://[2606:2800:21f::1]/",
])
def test_public_targets_are_allowed(url):
    assert check(url) == url


def test_dns_failure_is_not_an_error():
    assert check("https://not-yet-live.example/") == "https://not-yet-live.example/"


def test_allow_private_skips_the_address_check():
    assert check("http://127.0.0.1:9000/", allow_private=True) == "http://127.0.0.1:9000/"
    assert check("http://localhost/", allow_private=True, resolve=resolver({})) == "http://localhost/"


def test_scheme_and_host_are_normalised_to_lower_case():
    assert check("HTTPS://Example.ORG/Path") == "https://example.org/Path"


@pytest.mark.parametrize("url, message", [
    ("", "required"),
    ("   ", "required"),
    ("ftp://example.org", "http:// or https://"),
    ("javascript:alert(1)", "http:// or https://"),
    ("example.org", "http:// or https://"),
    ("https://", "host"),
    ("https://user:pass@example.org", "username or password"),
    ("https://user@example.org", "username or password"),
    ("https://example.org:99999", "Invalid URL"),
    ("https://exa mple.org", "spaces"),
    ("https://example.org/" + "a" * 2000, "2000"),
])
def test_malformed_urls_are_rejected(url, message):
    with pytest.raises(InvalidMonitor, match=message):
        check(url)


def test_length_limit_is_inclusive():
    url = "https://example.org/"
    url += "a" * (2000 - len(url))
    assert check(url) == url


def test_blocked_target_is_a_value_error():
    assert issubclass(BlockedTarget, InvalidMonitor)
    assert issubclass(InvalidMonitor, ValueError)
