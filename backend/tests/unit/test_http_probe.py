"""HTTP uptime-probe tests using ``httpx.MockTransport`` — no real network.

Covers: status matching (default and custom expected codes), case-insensitive
keyword search with a body-size cap, redirects (followed and looping), and
timeout / refused / TLS / unexpected failures all becoming a "down" result
instead of an exception.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import time

import httpx

from backend.ssl_monitor.core.http_probe import (
    MAX_REDIRECTS,
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
