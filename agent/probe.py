"""TLS certificate probe — standalone, no CertWatch dependencies."""

from __future__ import annotations

import ipaddress
import socket
import ssl
from datetime import datetime, timezone
from dataclasses import dataclass

from cryptography import x509


def is_private_host(host: str) -> bool:
    """Return True for private/internal hostnames or private IPs."""
    name = (host or "").strip().lower()
    if not name:
        return False

    if name in {"localhost", "localhost.localdomain"}:
        return True

    labels = name.split(".")
    private_labels = {
        "localhost",
        "local",
        "internal",
        "lan",
        "home",
        "corp",
        "intranet",
        "localdomain",
    }
    if any(label in private_labels for label in labels):
        return True

    try:
        infos = socket.getaddrinfo(name, None)
    except socket.gaierror:
        return False

    for family, _, _, _, sockaddr in infos:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


@dataclass
class ProbeResult:
    """Raw facts about one TLS endpoint.

    Deliberately carries no status/days_remaining: classification and threshold
    rules belong to the server, which owns THRESHOLD_DAYS and already has that
    logic tested. An agent that classified locally would drift from the server's
    vocabulary and thresholds — which is exactly what used to happen.
    """

    domain: str
    port: int
    reachable: bool
    not_after: datetime | None = None
    error: str | None = None
    private: bool = False


def probe(host: str, port: int = 443, timeout: float = 10.0) -> ProbeResult:
    private = is_private_host(host)
    try:
        # Verification is intentionally disabled: we must read notAfter even for
        # expired / self-signed / untrusted-chain certs. Trust is not our job.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)

        if not der:
            return ProbeResult(host, port, reachable=False, error="no certificate returned", private=private)

        cert = x509.load_der_x509_certificate(der)
        not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") \
            else cert.not_valid_after.replace(tzinfo=timezone.utc)

        return ProbeResult(
            domain=host,
            port=port,
            reachable=True,
            not_after=not_after,
            private=private,
        )

    except (socket.timeout, TimeoutError) as e:
        return ProbeResult(host, port, reachable=False, error=f"timeout: {e}", private=private)
    except ssl.SSLError as e:
        return ProbeResult(host, port, reachable=False, error=f"tls error: {e}", private=private)
    except socket.gaierror as e:
        return ProbeResult(host, port, reachable=False, error=f"dns failure: {e}", private=private)
    except Exception as e:
        return ProbeResult(host, port, reachable=False, error=f"{type(e).__name__}: {e}", private=private)
