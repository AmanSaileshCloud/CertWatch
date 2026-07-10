"""TLS certificate probe.

The actual socket/TLS I/O is isolated behind an injectable ``Connector`` so the
threshold and parsing logic can be unit-tested with zero network access. The
default connector uses the stdlib ``ssl`` module with verification disabled so
the leaf certificate can be read even when it is expired or self-signed — we are
reporting on the cert, not trusting it.

Every failure mode is caught and turned into an ``unreachable`` ProbeResult; an
exception never leaks to the caller, and a down host is never mistaken for an
expiring one.
"""

from __future__ import annotations

import socket
import ssl
from collections.abc import Callable
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.x509.oid import NameOID

from .models import CertInfo, ProbeResult

# (host, port, timeout_seconds) -> DER-encoded leaf certificate bytes, or raises.
Connector = Callable[[str, int, float], bytes]

# DER bytes -> UTC-aware notAfter datetime.
Parser = Callable[[bytes], datetime]


def _default_connector(host: str, port: int, timeout: float) -> bytes:
    """Open a TLS connection (SNI = host) and return the leaf cert as DER bytes.

    Verification is intentionally disabled: we must read notAfter even for
    expired / self-signed / untrusted-chain certificates. Trust is not our job.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise ssl.SSLError("peer returned no certificate")
    return der


def _default_parser(der: bytes) -> datetime:
    """Parse the leaf certificate's notAfter as a UTC-aware datetime."""
    cert = x509.load_der_x509_certificate(der)
    # not_valid_after_utc is timezone-aware (UTC); available in cryptography >= 42.
    return cert.not_valid_after_utc


def probe_cert(
    host: str,
    port: int = 443,
    timeout: float = 5.0,
    *,
    connector: Connector = _default_connector,
    parser: Parser = _default_parser,
) -> ProbeResult:
    """Probe ``host:port`` over TLS and return a :class:`ProbeResult`.

    Reachable + parsed → ``reachable=True`` with a UTC ``not_after`` (even if the
    cert is already expired or self-signed). Any connection/handshake/parse
    failure → ``reachable=False`` with a short ``error`` and ``not_after=None``.
    """
    try:
        der = connector(host, port, timeout)
        not_after = parser(der)
    except (socket.timeout, TimeoutError) as exc:
        return ProbeResult(host, port, reachable=False, error=f"timeout: {exc}")
    except ssl.SSLError as exc:
        return ProbeResult(host, port, reachable=False, error=f"tls error: {exc}")
    except (ConnectionRefusedError, ConnectionResetError) as exc:
        return ProbeResult(host, port, reachable=False, error=f"connection refused: {exc}")
    except socket.gaierror as exc:
        return ProbeResult(host, port, reachable=False, error=f"dns failure: {exc}")
    except OSError as exc:
        return ProbeResult(host, port, reachable=False, error=f"network error: {exc}")
    except Exception as exc:  # noqa: BLE001 - last-resort guard; never leak.
        return ProbeResult(host, port, reachable=False, error=f"{type(exc).__name__}: {exc}")

    return ProbeResult(host, port, reachable=True, not_after=not_after)


def _friendly_name(name: x509.Name) -> str:
    """Common name, else organization, else the full RFC4514 string."""
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
        attrs = name.get_attributes_for_oid(oid)
        if attrs:
            return str(attrs[0].value)
    return name.rfc4514_string()


def _key_summary(pub) -> tuple[str, int | None]:
    if isinstance(pub, rsa.RSAPublicKey):
        return "RSA", pub.key_size
    if isinstance(pub, ec.EllipticCurvePublicKey):
        return f"EC ({pub.curve.name})", pub.key_size
    if isinstance(pub, ed25519.Ed25519PublicKey):
        return "Ed25519", 256
    if isinstance(pub, ed448.Ed448PublicKey):
        return "Ed448", 448
    if isinstance(pub, dsa.DSAPublicKey):
        return "DSA", pub.key_size
    return type(pub).__name__, getattr(pub, "key_size", None)


def extract_cert_info(der: bytes) -> CertInfo:
    """Parse a leaf certificate's human-readable details from DER bytes."""
    cert = x509.load_der_x509_certificate(der)

    sans: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = list(ext.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass

    key_type, key_bits = _key_summary(cert.public_key())
    sig = getattr(cert.signature_algorithm_oid, "_name", None) or cert.signature_algorithm_oid.dotted_string

    return CertInfo(
        issuer=_friendly_name(cert.issuer),
        subject=_friendly_name(cert.subject),
        serial=format(cert.serial_number, "x"),
        sig_algorithm=sig,
        key_type=key_type,
        key_bits=key_bits,
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        sans=sans,
    )


def probe_cert_info(
    host: str,
    port: int = 443,
    timeout: float = 5.0,
    *,
    connector: Connector = _default_connector,
) -> CertInfo:
    """Connect to ``host:port`` and return full leaf-cert details.

    Raises on any connection/handshake/parse failure — the caller (an on-demand
    detail endpoint) maps that to an error response.
    """
    der = connector(host, port, timeout)
    if not der:
        raise ssl.SSLError("peer returned no certificate")
    return extract_cert_info(der)
