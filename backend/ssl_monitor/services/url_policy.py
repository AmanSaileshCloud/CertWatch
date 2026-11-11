"""Which URLs a website monitor may check.

The uptime worker fetches whatever URL a user saves, from inside our network. Without
a guard, a user could point it at ``http://169.254.169.254/`` (cloud credentials) or
an internal admin page. So unless private targets are explicitly allowed, the host
must not be — or resolve to — a non-public address (see ``core.net_policy``).
The worker re-checks every request, redirect hops included, at probe time
(``core.http_probe``); this up-front check gives the user a clear error when saving.

A host that does not resolve at all is accepted: a site may be added before its DNS
exists, and will simply show DOWN until it does.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from ..core.net_policy import (
    IpAddress,
    is_blocked_address,
    parse_ip_literal,
    strip_trailing_dot,
)
from .errors import BlockedTarget, InvalidMonitor

MAX_URL_LENGTH = 2000
ALLOWED_SCHEMES = ("http", "https")

Resolver = Callable[..., Iterable[tuple]]


def validate_monitor_url(
    url: str, *, allow_private: bool, resolve: Resolver = socket.getaddrinfo
) -> str:
    """Return the normalised URL, or raise InvalidMonitor / BlockedTarget."""
    url = (url or "").strip()
    host = _check_shape(url)
    if not allow_private:
        _check_not_private(host, resolve)
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _check_shape(url: str) -> str:
    if not url:
        raise InvalidMonitor("URL is required")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidMonitor(f"URL must be at most {MAX_URL_LENGTH} characters")
    if any(ch.isspace() for ch in url):
        raise InvalidMonitor("URL must not contain spaces")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError as exc:
        raise InvalidMonitor(f"Invalid URL: {exc}") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidMonitor("URL must start with http:// or https://")
    if "@" in parts.netloc:
        raise InvalidMonitor("URL must not contain a username or password")
    if not parts.hostname:
        raise InvalidMonitor("URL must include a host name")
    return parts.hostname


def _check_not_private(host: str, resolve: Resolver) -> None:
    for address in _addresses_of(host, resolve):
        if is_blocked_address(address):
            raise BlockedTarget(
                f"URL points to a private or internal address ({address}); set "
                "UPTIME_ALLOW_PRIVATE_TARGETS=true to allow internal sites."
            )


def _addresses_of(host: str, resolve: Resolver) -> list[IpAddress]:
    literal = parse_ip_literal(host)
    if literal is not None:
        return [literal]
    try:
        infos = resolve(strip_trailing_dot(host), None)
    except (OSError, UnicodeError):
        return []  # DNS failure is not an error — the monitor just shows DOWN
    parsed = (parse_ip_literal(str(info[4][0])) for info in infos)
    return [address for address in parsed if address is not None]
