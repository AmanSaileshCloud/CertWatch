"""Which network addresses the uptime worker may contact (SSRF guard). Pure — no I/O.

An address is blocked unless it is globally reachable: loopback, private, link-local
(cloud metadata at 169.254.169.254), carrier-grade NAT, benchmark, reserved and
multicast ranges are all refused. IPv6 forms that embed an IPv4 address
(``::ffff:a.b.c.d``, ``::a.b.c.d``, 6to4 ``2002::/16``, NAT64 ``64:ff9b::/96``) are
judged by the IPv4 address inside them.
"""

from __future__ import annotations

import ipaddress
import re
import socket

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")
_IPV4_COMPATIBLE_PREFIX = ipaddress.IPv6Network("::/96")
_IPV4_BITS = 0xFFFFFFFF
# Hosts made only of these characters may be a legacy numeric IPv4 form that
# resolvers still accept: 2130706433, 0x7f.1, 0177.0.0.1, 127.1, 0.
_LEGACY_IPV4_RE = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)


def is_blocked_address(address: IpAddress) -> bool:
    """True if the worker must not connect to ``address``."""
    address = _embedded_ipv4(address) or address
    return not address.is_global or address.is_multicast


def parse_ip_literal(host: str) -> IpAddress | None:
    """The IP address ``host`` denotes, or None if it is a hostname.

    Accepts standard IPv4/IPv6 (an IPv6 zone id is dropped), the legacy numeric IPv4
    forms, and one trailing dot."""
    host = strip_trailing_dot(host).split("%", 1)[0]
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return _parse_legacy_ipv4(host)


def strip_trailing_dot(host: str) -> str:
    """``example.com.`` (fully-qualified form) → ``example.com``."""
    return host[:-1] if host.endswith(".") else host


def _parse_legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    if not _LEGACY_IPV4_RE.match(host):
        return None
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def _embedded_ipv4(address: IpAddress) -> ipaddress.IPv4Address | None:
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64_PREFIX or address in _IPV4_COMPATIBLE_PREFIX:
        return ipaddress.IPv4Address(int(address) & _IPV4_BITS)
    return None
