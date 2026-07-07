"""Shared fixtures.

Certificates are generated in-memory rather than committed as static PEM files:
a checked-in "valid" cert would itself expire and make the suite fail over time.
The generator builds self-signed certs with arbitrary validity windows, which
also gives us the expired and self-signed cases for free.
"""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID


def make_cert_der(
    *,
    not_before: dt.datetime,
    not_after: dt.datetime,
    common_name: str = "example.test",
    issuer_name: str | None = None,
) -> bytes:
    """Build a self-signed certificate and return its DER bytes.

    If ``issuer_name`` differs from ``common_name`` the cert merely *looks* like
    it was issued by another CA; it is still self-signed (signed by its own key),
    which is exactly the untrusted-chain case the probe must handle.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, issuer_name or common_name)]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(Encoding.DER)


@pytest.fixture(scope="session")
def now() -> dt.datetime:
    return dt.datetime(2026, 6, 26, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="session")
def valid_cert_der(now: dt.datetime) -> bytes:
    """Valid cert expiring ~90 days out."""
    return make_cert_der(
        not_before=now - dt.timedelta(days=1),
        not_after=now + dt.timedelta(days=90),
    )


@pytest.fixture(scope="session")
def expiring_soon_cert_der(now: dt.datetime) -> bytes:
    """Valid but expiring in ~5 days (inside the 7-day threshold)."""
    return make_cert_der(
        not_before=now - dt.timedelta(days=300),
        not_after=now + dt.timedelta(days=5),
    )


@pytest.fixture(scope="session")
def expired_cert_der(now: dt.datetime) -> bytes:
    """Cert that expired ~10 days ago — must still parse."""
    return make_cert_der(
        not_before=now - dt.timedelta(days=400),
        not_after=now - dt.timedelta(days=10),
    )


@pytest.fixture(scope="session")
def self_signed_cert_der(now: dt.datetime) -> bytes:
    """Valid window, but issuer claims to be an untrusted external CA."""
    return make_cert_der(
        not_before=now - dt.timedelta(days=1),
        not_after=now + dt.timedelta(days=60),
        common_name="self.example.test",
        issuer_name="Untrusted Root CA",
    )
