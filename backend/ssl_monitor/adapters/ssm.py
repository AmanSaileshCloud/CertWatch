"""AWS Systems Manager probe adapter for private-domain checks."""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..core.models import DomainRecord, ProbeResult

logger = logging.getLogger("ssl_monitor.ssm")

_PROBE_SCRIPT = r"""import json, socket, ssl, sys, tempfile, _ssl
from datetime import datetime, timezone

host = sys.argv[1]
port = int(sys.argv[2])
timeout = float(sys.argv[3])
try:
    # Read the leaf certificate even when it is expired or privately signed.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            pem = ssl.DER_cert_to_PEM_cert(tls.getpeercert(binary_form=True))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem") as cert_file:
        cert_file.write(pem)
        cert_file.flush()
        certificate = _ssl._test_decode_cert(cert_file.name)
    not_after = datetime.fromtimestamp(
        ssl.cert_time_to_seconds(certificate["notAfter"]), timezone.utc
    ).isoformat()
    print(json.dumps({"reachable": True, "not_after": not_after, "error": None}))
except Exception as exc:
    print(json.dumps({"reachable": False, "not_after": None,
                      "error": f"{type(exc).__name__}: {exc}"}))
"""


class SSMProbe:
    """Run the same certificate probe on an SSM-managed EC2 instance."""

    def __init__(
        self,
        client: Any,
        *,
        document_name: str = "AWS-RunShellScript",
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self.client = client
        self.document_name = document_name
        self.poll_interval_seconds = poll_interval_seconds

    def __call__(self, record: DomainRecord, timeout: float) -> ProbeResult:
        if not record.agent_id:
            return ProbeResult(record.host, record.port, False, error="SSM instance ID is missing")

        command = self._command(record.host, record.port, timeout)
        try:
            command_timeout = max(30, int(timeout) + 5)
            response = self.client.send_command(
                InstanceIds=[record.agent_id],
                DocumentName=self.document_name,
                Parameters={"commands": [command]},
                # AWS requires SendCommand.TimeoutSeconds to be at least 30.
                TimeoutSeconds=command_timeout,
            )
            command_id = response["Command"]["CommandId"]
            invocation = self._wait_for_result(command_id, record.agent_id, command_timeout)
            if invocation.get("Status") != "Success":
                return ProbeResult(
                    record.host,
                    record.port,
                    False,
                    error=invocation.get("StatusDetails") or invocation.get("Status") or "SSM command failed",
                )
            return self._parse_output(record, invocation.get("StandardOutputContent", ""))
        except Exception as exc:
            logger.warning("SSM probe failed for %s on %s: %s", record.domain, record.agent_id, exc)
            return ProbeResult(record.host, record.port, False, error=f"SSM error: {exc}")

    @staticmethod
    def _command(host: str, port: int, timeout: float) -> str:
        encoded = base64.b64encode(_PROBE_SCRIPT.encode()).decode("ascii")
        return f"echo {encoded} | base64 -d | python3 - {host!r} {port} {timeout}"

    def _wait_for_result(self, command_id: str, instance_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout + 5
        while True:
            try:
                result = self.client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except Exception as exc:
                error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if error_code != "InvocationDoesNotExist":
                    raise
                if time.monotonic() >= deadline:
                    return {"Status": "TimedOut", "StatusDetails": "SSM invocation was not created"}
                time.sleep(self.poll_interval_seconds)
                continue
            if result.get("Status") not in {"Pending", "InProgress", "Delayed"}:
                return result
            if time.monotonic() >= deadline:
                return {"Status": "TimedOut", "StatusDetails": "SSM command polling timed out"}
            time.sleep(self.poll_interval_seconds)

    @staticmethod
    def _parse_output(record: DomainRecord, output: str) -> ProbeResult:
        try:
            payload = json.loads(output.strip().splitlines()[-1])
            expiry = payload.get("not_after")
            not_after = datetime.fromisoformat(expiry) if expiry else None
            if not_after and not_after.tzinfo is None:
                not_after = not_after.replace(tzinfo=timezone.utc)
            return ProbeResult(
                record.host,
                record.port,
                bool(payload.get("reachable")),
                not_after=not_after,
                error=payload.get("error"),
            )
        except (ValueError, IndexError, json.JSONDecodeError) as exc:
            return ProbeResult(record.host, record.port, False, error=f"invalid SSM probe output: {exc}")


def make_ssm_probe(settings: Any) -> SSMProbe:
    """Build an SSM probe using the application's shared AWS settings."""
    import boto3

    return SSMProbe(
        boto3.client("ssm", **settings.boto_kwargs()),
        document_name=settings.ssm_document_name,
        poll_interval_seconds=settings.ssm_poll_interval_seconds,
    )