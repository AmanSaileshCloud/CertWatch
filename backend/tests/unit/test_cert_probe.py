"""Cert-probe tests using injected connectors — no real network.

Covers: valid, expiring-soon, expired, and self-signed certs all parse to a
reachable result with a UTC notAfter; and connection-refused / timeout / DNS /
generic-OSError / empty-cert all become an ``unreachable`` result that is never
mistaken for an expiring one.
"""

from __future__ import annotations

import datetime as dt
import socket
import ssl

import pytest

from backend.ssl_monitor.core.cert_probe import probe_cert
from backend.ssl_monitor.core.models import Status
from backend.ssl_monitor.core.status import classify_status, days_remaining

THRESHOLDS = [30, 14, 7, 1]
UTC = dt.timezone.utc


def _connector_returning(der: bytes):
    def connector(host: str, port: int, timeout: float) -> bytes:
        return der
    return connector


def _connector_raising(exc: BaseException):
    def connector(host: str, port: int, timeout: float) -> bytes:
        raise exc
    return connector


# ---- reachable cases -------------------------------------------------------

def test_valid_cert_is_reachable_with_future_expiry(valid_cert_der, now):
    res = probe_cert("example.test", connector=_connector_returning(valid_cert_der))
    assert res.reachable is True
    assert res.error is None
    assert res.not_after is not None and res.not_after.tzinfo is not None
    assert days_remaining(res.not_after, now) > 30
    assert classify_status(res.reachable, days_remaining(res.not_after, now), THRESHOLDS) is Status.OK


def test_expiring_soon_cert_classifies_warning(expiring_soon_cert_der, now):
    res = probe_cert("soon.test", connector=_connector_returning(expiring_soon_cert_der))
    assert res.reachable is True
    d = days_remaining(res.not_after, now)
    assert 0 <= d <= 7
    assert classify_status(res.reachable, d, THRESHOLDS) is Status.WARNING


def test_expired_cert_still_parses_and_classifies_expired(expired_cert_der, now):
    res = probe_cert("old.test", connector=_connector_returning(expired_cert_der))
    assert res.reachable is True  # we reached it and read the cert
    d = days_remaining(res.not_after, now)
    assert d < 0
    assert classify_status(res.reachable, d, THRESHOLDS) is Status.EXPIRED


def test_self_signed_cert_is_read_not_rejected(self_signed_cert_der, now):
    res = probe_cert("self.example.test", connector=_connector_returning(self_signed_cert_der))
    assert res.reachable is True
    assert res.not_after is not None
    assert classify_status(res.reachable, days_remaining(res.not_after, now), THRESHOLDS) is Status.OK


# ---- unreachable cases -----------------------------------------------------

@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("refused"),
        TimeoutError("timed out"),
        socket.timeout("timed out"),
        socket.gaierror("name resolution failed"),
        ssl.SSLError("handshake failure"),
        OSError("network unreachable"),
        ConnectionResetError("reset by peer"),
    ],
)
def test_connection_failures_are_unreachable_not_expiring(exc, now):
    res = probe_cert("down.test", connector=_connector_raising(exc))
    assert res.reachable is False
    assert res.not_after is None
    assert res.error is not None
    # The critical invariant: a down host is never an alertable cert status.
    status = classify_status(res.reachable, None, THRESHOLDS)
    assert status is Status.UNREACHABLE
    assert status not in (Status.WARNING, Status.EXPIRED, Status.OK)


def test_empty_certificate_is_unreachable(now):
    """A handshake that yields no peer cert must not crash or false-alert."""
    res = probe_cert("nocert.test", connector=_connector_returning(b""))
    assert res.reachable is False
    assert res.not_after is None


def test_probe_never_raises(now):
    """Even an unexpected error type is swallowed into an unreachable result."""
    res = probe_cert("weird.test", connector=_connector_raising(RuntimeError("boom")))
    assert res.reachable is False
    assert "boom" in res.error


def test_default_parser_round_trips_der(valid_cert_der):
    """The real default parser (cryptography) reads notAfter from DER."""
    res = probe_cert("example.test", connector=_connector_returning(valid_cert_der))
    assert isinstance(res.not_after, dt.datetime)
    assert res.not_after.tzinfo is not None


def test_extract_cert_info_reads_fields(valid_cert_der):
    from backend.ssl_monitor.core.cert_probe import extract_cert_info

    info = extract_cert_info(valid_cert_der)
    assert info.subject == "example.test"
    assert info.key_type == "RSA" and info.key_bits == 2048
    assert "example.test" in info.sans
    assert info.serial and info.not_after is not None and info.not_before is not None
    assert "sha256" in info.sig_algorithm.lower()
