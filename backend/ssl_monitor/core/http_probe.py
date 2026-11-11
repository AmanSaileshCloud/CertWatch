"""HTTP uptime probe.

One GET against a monitor's URL, judged against its expected status code and
optional keyword. The ``httpx.AsyncClient`` is injected, so tests can swap in an
``httpx.MockTransport`` and run with zero network access.

Every failure (timeout, refused connection, TLS error, redirect loop, anything
unexpected) becomes a "down" :class:`CheckResult` with a short, human-readable
``error``. Only ``asyncio.CancelledError`` propagates, so shutdown still works.

SSRF guard: the client from :func:`make_http_client` refuses, at request time, any
request — every redirect hop included — whose host is or resolves to a non-public
address (see :mod:`.net_policy`). The host is resolved just before httpx connects,
so a tiny DNS-rebinding window (resolve vs. connect) remains; on AWS, also set the
instance metadata to IMDSv2 with hop limit 1 as belt-and-braces.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from .net_policy import IpAddress, is_blocked_address, parse_ip_literal, strip_trailing_dot
from .uptime_models import DEFAULT_REGION, CheckResult, Monitor

MAX_REDIRECTS = 5
MAX_ERROR_CHARS = 500
DEFAULT_MAX_BODY_BYTES = 1_000_000


# (host, port) -> getaddrinfo-style tuples, as ``loop.getaddrinfo`` returns.
AsyncResolver = Callable[[str, int], Awaitable[list[tuple]]]


class BlockedAddressError(Exception):
    """A request's host is, or resolves to, a private or internal address."""

    def __init__(self, host: str) -> None:
        super().__init__(f"Blocked: {host} resolves to a private or internal address")
        self.host = host


async def _system_resolve(host: str, port: int) -> list[tuple]:
    return await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)


class GuardedTransport(httpx.AsyncBaseTransport):
    """Wraps a transport; refuses requests whose host is or resolves to a blocked
    address. httpx sends each redirect hop through the transport, so hops are checked
    too. A host that does not resolve is passed on (the inner transport then fails)."""

    def __init__(
        self, inner: httpx.AsyncBaseTransport, resolve: AsyncResolver = _system_resolve
    ) -> None:
        self._inner = inner
        self._resolve = resolve

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._require_public(request.url.host, request.url.port)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def _require_public(self, host: str, port: int | None) -> None:
        literal = parse_ip_literal(host)
        addresses = [literal] if literal is not None else await self._resolved(host, port)
        if any(is_blocked_address(address) for address in addresses):
            raise BlockedAddressError(host)

    async def _resolved(self, host: str, port: int | None) -> list[IpAddress]:
        try:
            infos = await self._resolve(strip_trailing_dot(host), port or 0)
        except (OSError, UnicodeError):
            return []
        parsed = (parse_ip_literal(str(info[4][0])) for info in infos)
        return [address for address in parsed if address is not None]


def make_http_client(
    user_agent: str,
    max_connections: int,
    *,
    allow_private: bool = False,
    resolve: AsyncResolver = _system_resolve,
) -> httpx.AsyncClient:
    """Build the shared client: follows redirects, verifies TLS, sends our User-Agent,
    and — unless ``allow_private`` — refuses private/internal targets on every hop."""
    transport: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport(
        verify=True, limits=httpx.Limits(max_connections=max_connections)
    )
    if not allow_private:
        transport = GuardedTransport(transport, resolve)
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": user_agent},
        trust_env=False,  # ignore HTTP(S)_PROXY: a proxy would bypass the address guard
    )


async def probe_http(
    client: httpx.AsyncClient,
    monitor: Monitor,
    *,
    now: datetime,
    region: str = DEFAULT_REGION,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> CheckResult:
    """Check ``monitor`` once and return the outcome. Never raises (bar cancellation)."""
    try:
        status_code, body, response_ms = await _fetch(client, monitor, max_body_bytes)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a "down" result.
        return _down(monitor, now, region, error=_describe_failure(exc, monitor))

    error = _judge(monitor, status_code, body)
    return CheckResult(
        monitor_id=monitor.id,
        checked_at=now,
        is_up=error is None,
        status_code=status_code,
        response_ms=response_ms,
        error=_truncate(error),
        region=region,
    )


async def _fetch(
    client: httpx.AsyncClient, monitor: Monitor, max_body_bytes: int
) -> tuple[int, str | None, int]:
    """GET the URL; return (status, decoded body or None, elapsed ms).

    ``timeout_seconds`` caps the WHOLE check — redirects and body included — not
    just each network read, so a slow-trickling server can't stall the cycle.
    Elapsed time is what a visitor waits: all redirect hops (+ body for keyword checks).
    """
    started = time.perf_counter()
    timeout = httpx.Timeout(monitor.timeout_seconds)
    async with (
        asyncio.timeout(monitor.timeout_seconds),
        client.stream("GET", monitor.url, timeout=timeout) as response,
    ):
        body = await _read_body(response, max_body_bytes) if monitor.keyword else None
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    return response.status_code, body, elapsed_ms


async def _read_body(response: httpx.Response, max_body_bytes: int) -> str:
    """Read at most ``max_body_bytes`` of the body and decode it leniently."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= max_body_bytes:
            break
    raw = b"".join(chunks)[:max_body_bytes]
    return raw.decode(response.encoding or "utf-8", errors="replace")


def _judge(monitor: Monitor, status_code: int, body: str | None) -> str | None:
    """Return an error message if the response fails the monitor's rules, else None."""
    if status_code != monitor.expected_status:
        return f"Expected {monitor.expected_status}, got {status_code}"
    if monitor.keyword and monitor.keyword.lower() not in (body or "").lower():
        return f'Keyword "{monitor.keyword}" not found'
    return None


def _describe_failure(exc: Exception, monitor: Monitor) -> str:
    if isinstance(exc, BlockedAddressError):
        return str(exc)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return f"Timed out after {monitor.timeout_seconds}s"
    if isinstance(exc, httpx.TooManyRedirects):
        return f"Too many redirects (>{MAX_REDIRECTS})"
    if isinstance(exc, httpx.ConnectError):
        return _describe_connect_error(exc)
    return f"{type(exc).__name__}: {exc}"


def _describe_connect_error(exc: httpx.ConnectError) -> str:
    ssl_error = _find_ssl_error(exc)
    if ssl_error is not None:
        return f"SSL error: {_ssl_reason(ssl_error)}"
    return f"Connection failed: {str(exc) or type(exc).__name__}"


def _find_ssl_error(exc: BaseException) -> ssl.SSLError | None:
    """Walk the cause/context chain looking for an ``ssl.SSLError``."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _ssl_reason(error: ssl.SSLError) -> str:
    verify_message = getattr(error, "verify_message", None)
    reason = getattr(error, "reason", None)
    return verify_message or reason or str(error) or type(error).__name__


def _down(monitor: Monitor, now: datetime, region: str, *, error: str) -> CheckResult:
    return CheckResult(
        monitor_id=monitor.id,
        checked_at=now,
        is_up=False,
        error=_truncate(error),
        region=region,
    )


def _truncate(error: str | None) -> str | None:
    return error[:MAX_ERROR_CHARS] if error else error
