"""HTTP uptime-probe tests using ``httpx.MockTransport`` — no real network.

Covers: status matching (default and custom expected codes), case-insensitive
keyword search with a body-size cap, redirects (followed and looping), and
timeout / refused / TLS / unexpected failures all becoming a "down" result
instead of an exception.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
import ssl
import time

import httpx

from backend.ssl_monitor.core.http_probe import (
    MAX_REDIRECTS,
    GuardedTransport,
    make_http_client,
    probe_http,
)
from backend.ssl_monitor.core.uptime_models import CheckResult, Monitor

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)
URL = "https://example.test/"


def _monitor(**overrides) -> Monitor:
    fields = {"id": 1, "name": "Example", "url": URL, "interval_seconds": 60}
    fields.update(overrides)
    return Monitor(**fields)


def _probe(handler, monitor: Monitor | None = None, **kwargs) -> CheckResult:
    async def run() -> CheckResult:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True, max_redirects=MAX_REDIRECTS
        ) as client:
            return await probe_http(client, monitor or _monitor(), now=NOW, **kwargs)

    return asyncio.run(run())


def _respond(status: int, text: str = ""):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=text)
    return handler


def _raise(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc
    return handler


def test_200_is_up():
    result = _probe(_respond(200))
    assert result.is_up
    assert result.status_code == 200
    assert result.error is None


def test_503_is_down_with_status_message():
    result = _probe(_respond(503))
    assert not result.is_up
    assert result.status_code == 503
    assert result.error == "Expected 200, got 503"
    assert result.response_ms is not None


def test_custom_expected_status_is_up():
    result = _probe(_respond(401), _monitor(expected_status=401))
    assert result.is_up
    assert result.status_code == 401


def test_keyword_found_case_insensitively():
    result = _probe(_respond(200, "<h1>Welcome Home</h1>"), _monitor(keyword="welcome HOME"))
    assert result.is_up


def test_keyword_missing_is_down():
    result = _probe(_respond(200, "<h1>Maintenance</h1>"), _monitor(keyword="Welcome"))
    assert not result.is_up
    assert result.status_code == 200
    assert result.error == 'Keyword "Welcome" not found'


def test_keyword_beyond_body_limit_is_not_found():
    body = "x" * 50 + "needle"
    result = _probe(_respond(200, body), _monitor(keyword="needle"), max_body_bytes=10)
    assert not result.is_up
    assert result.error == 'Keyword "needle" not found'


def test_timeout_is_down_without_status_or_timing():
    result = _probe(_raise(httpx.ReadTimeout("slow")), _monitor(timeout_seconds=7))
    assert not result.is_up
    assert result.error == "Timed out after 7s"
    assert result.status_code is None
    assert result.response_ms is None


def test_connection_refused_is_down():
    result = _probe(_raise(httpx.ConnectError("[Errno 111] Connection refused")))
    assert not result.is_up
    assert result.error == "Connection failed: [Errno 111] Connection refused"
    assert result.status_code is None
    assert result.response_ms is None


def test_connect_error_without_message_falls_back_to_class_name():
    result = _probe(_raise(httpx.ConnectError("")))
    assert result.error == "Connection failed: ConnectError"


def test_ssl_failure_is_reported_as_ssl_error():
    def handler(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError(1, "certificate verify failed")
        except ssl.SSLError as cause:
            raise httpx.ConnectError("handshake failed") from cause

    result = _probe(handler)
    assert not result.is_up
    assert result.error.startswith("SSL error: ")
    assert "certificate verify failed" in result.error
    assert result.status_code is None


def test_redirect_is_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(301, headers={"Location": "/home"})
        return httpx.Response(200, text="ok")

    result = _probe(handler)
    assert result.is_up
    assert result.status_code == 200


def test_redirect_loop_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/"})

    result = _probe(handler)
    assert not result.is_up
    assert result.error == f"Too many redirects (>{MAX_REDIRECTS})"
    assert result.status_code is None


def test_unexpected_exception_does_not_raise():
    result = _probe(_raise(ValueError("boom")))
    assert not result.is_up
    assert result.error == "ValueError: boom"


def test_long_error_is_truncated():
    result = _probe(_raise(ValueError("x" * 2000)))
    assert len(result.error) == 500


def test_response_ms_is_non_negative_int():
    result = _probe(_respond(200))
    assert isinstance(result.response_ms, int)
    assert result.response_ms >= 0


def test_checked_at_and_region_are_passed_through():
    result = _probe(_respond(200), region="eu-west")
    assert result.checked_at == NOW
    assert result.region == "eu-west"
    assert result.monitor_id == 1


def test_cancellation_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    try:
        _probe(handler)
    except asyncio.CancelledError:
        return
    raise AssertionError("CancelledError was swallowed")


def test_make_http_client_configuration():
    async def build() -> httpx.AsyncClient:
        return make_http_client("CertWatch-Test/1.0", max_connections=3)

    client = asyncio.run(build())
    try:
        assert client.headers["User-Agent"] == "CertWatch-Test/1.0"
        assert client.follow_redirects is True
        assert client.max_redirects == MAX_REDIRECTS
    finally:
        asyncio.run(client.aclose())


def test_timeout_caps_the_whole_check_not_each_read():
    async def trickle():
        for _ in range(50):
            yield b"x"
            await asyncio.sleep(0.3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=trickle())

    started = time.perf_counter()
    result = _probe(handler, _monitor(timeout_seconds=1, keyword="never-there"))

    assert time.perf_counter() - started < 3
    assert result.is_up is False
    assert result.error == "Timed out after 1s"


# ── SSRF guard (GuardedTransport) ────────────────────────────────

PUBLIC_IP = "93.184.215.14"


def _fake_resolver(hosts: dict[str, str]):
    """Async getaddrinfo stand-in: unknown hosts fail to resolve."""
    async def resolve(host: str, port: int) -> list[tuple]:
        if host not in hosts:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], port))]
    return resolve


def _guarded_probe(handler, url: str, hosts: dict[str, str]) -> tuple[CheckResult, list[str]]:
    """Probe through GuardedTransport wrapping a MockTransport; also return requested URLs."""
    requested: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return handler(request)

    async def run() -> CheckResult:
        transport = GuardedTransport(httpx.MockTransport(recording), _fake_resolver(hosts))
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True, max_redirects=MAX_REDIRECTS
        ) as client:
            return await probe_http(client, _monitor(url=url), now=NOW)

    return asyncio.run(run()), requested


def _redirect_to(location: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200)
    return handler


def test_guard_allows_public_host():
    result, requested = _guarded_probe(_respond(200), URL, {"example.test": PUBLIC_IP})
    assert result.is_up
    assert requested == [URL]


def test_guard_blocks_redirect_to_metadata_address():
    result, requested = _guarded_probe(
        _redirect_to("http://169.254.169.254/latest/meta-data/"), URL,
        {"example.test": PUBLIC_IP},
    )
    assert result.is_up is False
    assert result.error == "Blocked: 169.254.169.254 resolves to a private or internal address"
    assert requested == [URL]  # the metadata request was never sent


def test_guard_blocks_hostname_resolving_privately():
    result, requested = _guarded_probe(
        _respond(200), "https://intranet.test/", {"intranet.test": "10.0.0.8"})
    assert result.is_up is False
    assert result.error == "Blocked: intranet.test resolves to a private or internal address"
    assert requested == []


def test_guard_blocks_legacy_numeric_ip_literal():
    result, requested = _guarded_probe(_respond(200), "http://2130706433/", {})
    assert result.error == "Blocked: 2130706433 resolves to a private or internal address"
    assert requested == []


def test_guard_passes_unresolvable_host_to_the_transport():
    result, requested = _guarded_probe(_respond(200), "https://not-yet-live.test/", {})
    assert result.is_up and requested == ["https://not-yet-live.test/"]


def test_make_http_client_guards_by_default():
    async def check() -> CheckResult:
        resolve = _fake_resolver({"intranet.test": "10.0.0.8"})
        async with make_http_client("t", 1, resolve=resolve) as client:
            return await probe_http(client, _monitor(url="http://intranet.test/"), now=NOW)

    result = asyncio.run(check())
    assert result.error == "Blocked: intranet.test resolves to a private or internal address"


def test_make_http_client_without_guard_when_private_targets_allowed():
    async def transports() -> tuple[object, object]:
        async with (
            make_http_client("t", 1) as guarded,
            make_http_client("t", 1, allow_private=True) as unguarded,
        ):
            return guarded._transport, unguarded._transport

    guarded, unguarded = asyncio.run(transports())
    assert isinstance(guarded, GuardedTransport)
    assert not isinstance(unguarded, GuardedTransport)


def test_client_ignores_proxy_environment_so_the_guard_cannot_be_bypassed():
    async def build():
        return make_http_client("CertWatch-Test", 5)

    client = asyncio.run(build())
    try:
        assert client.trust_env is False
    finally:
        asyncio.run(client.aclose())
