"""Environment-driven configuration.

A single :class:`Settings` object is the only place env vars are read. Storage is
PostgreSQL (``DATABASE_URL``); AWS is used only for SES/SNS alerts (via ``boto_kwargs``).
Core logic never imports this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_THRESHOLD_DAYS = "30,14,7,1"

# The built-in placeholder secret. Refused at startup when auth is enabled.
INSECURE_AUTH_SECRET = "dev-insecure-change-me-please-set-a-real-AUTH_SECRET"

# A real HS256 secret should be at least 32 chars (`openssl rand -hex 32` = 64).
MIN_AUTH_SECRET_LENGTH = 32

# Substrings that mark a value as a template placeholder someone forgot to fill
# in. The shipped deploy/certwatch.env.example uses CHANGE_ME_*, and a copied-but-
# unedited env file is the single most likely way a real deployment ends up with
# a publicly-known signing key — so refuse to start on any of these.
_PLACEHOLDER_MARKERS = (
    "change_me",
    "changeme",
    "change-me",
    "your-secret",
    "yoursecret",
    "placeholder",
    "example",
    "xxxx",
)


def _placeholder_marker(value: str) -> str | None:
    """Return the placeholder substring found in ``value``, or None."""
    lowered = value.lower()
    return next((m for m in _PLACEHOLDER_MARKERS if m in lowered), None)


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency).

    Reads simple ``KEY=VALUE`` lines from ``path`` into the process environment
    using ``setdefault`` — so real environment variables always win over the
    file. Missing file is a no-op. Quotes and inline ``#`` are trimmed.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.split(" #", 1)[0].strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


def _parse_emails(raw: str) -> list[str]:
    """Parse ``"a@x.com, b@y.com"`` → ``["a@x.com", "b@y.com"]`` (deduped)."""
    seen: list[str] = []
    for part in raw.split(","):
        email = part.strip().lower()
        if email and email not in seen:
            seen.append(email)
    return seen


def _parse_thresholds(raw: str) -> list[int]:
    """Parse ``"30,14,7,1"`` → ``[30, 14, 7, 1]`` (deduped, descending)."""
    values = {int(part.strip()) for part in raw.split(",") if part.strip()}
    if not values:
        raise ValueError("THRESHOLD_DAYS must contain at least one integer")
    return sorted(values, reverse=True)


def _positive(kind: type, env: Mapping[str, str], key: str, default: str):
    """Read ``key`` as int/float and refuse zero or negative values."""
    value = kind(env.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be greater than 0 (got {value})")
    return value


def _required(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, default).strip()
    if not value:
        raise ValueError(f"{key} must not be empty")
    return value


@dataclass(frozen=True)
class Settings:
    backend: str  # "local" | "aws"
    storage: str  # "postgres" (default) | "sqlite" (legacy) | "memory" (dev/tests only)
    database_url: str | None  # PostgreSQL URL when storage=postgres
    sqlite_path: str  # DB file path when storage=sqlite (also the migration source)
    aws_region: str  # for SES/SNS
    aws_endpoint_url: str | None  # LocalStack URL locally; None on real AWS
    sns_topic_arn: str | None
    notifier: str  # "console" | "sns_ses"
    alert_email: str | None  # SES sender ("From") and default recipient
    alert_recipients: list[str]  # extra "To" addresses (ALERT_RECIPIENTS)
    threshold_days: list[int]
    tls_timeout_seconds: float
    # Hours an agent-managed domain may go without a report before the checker
    # treats its agent as down and alerts once.
    agent_stale_after_hours: float
    agent_transport: str  # retained name for the SSM instance-ID transport
    ssm_document_name: str
    ssm_poll_interval_seconds: float
    # Auth — username/password JWT (self-contained, no external service).
    auth_enabled: bool
    auth_secret: str
    token_expire_minutes: int
    users_file: str
    bootstrap_password: str  # seeds the first admin when the user store is empty
    # Uptime monitoring worker.
    uptime_tick_seconds: float = 10.0  # how often to look for due monitors
    uptime_max_concurrency: int = 50  # max sites checked at the same moment
    uptime_user_agent: str = "CertWatch-Uptime/1.0"
    uptime_region: str = "default"  # stamped on each result (multi-region later)
    uptime_max_body_bytes: int = 1_000_000  # page bytes read for the keyword check
    uptime_failure_threshold: int = 3  # failures in a row before a monitor is DOWN
    uptime_recovery_threshold: int = 1  # successes in a row before it is UP again
    # Allow monitors on private/loopback/link-local addresses (internal sites).
    # Off by default: stops the checker being pointed at e.g. the EC2 metadata service.
    uptime_allow_private_targets: bool = False

    @property
    def is_local(self) -> bool:
        return self.backend == "local"

    @property
    def auth_secret_problem(self) -> str | None:
        """Why ``AUTH_SECRET`` is unusable in production, or None if it's fine.

        Anyone holding the signing key can forge an admin token, so a weak or
        unedited secret is a full auth bypass — not a warning.
        """
        secret = self.auth_secret
        if not secret:
            return "AUTH_SECRET is empty."
        if secret == INSECURE_AUTH_SECRET:
            return "AUTH_SECRET is the built-in insecure default."
        marker = _placeholder_marker(secret)
        if marker:
            return (
                f"AUTH_SECRET still contains the placeholder text {marker!r} — it "
                "looks like a copied template that was never filled in."
            )
        if len(secret) < MIN_AUTH_SECRET_LENGTH:
            return (
                f"AUTH_SECRET is only {len(secret)} characters; at least "
                f"{MIN_AUTH_SECRET_LENGTH} are required."
            )
        return None

    @property
    def auth_secret_is_insecure(self) -> bool:
        return self.auth_secret_problem is not None

    @property
    def bootstrap_password_problem(self) -> str | None:
        """Why ``AUTH_BOOTSTRAP_PASSWORD`` is unusable, or None if it's fine.

        Only placeholder text is rejected here: this password seeds the first
        admin account, and a shipped CHANGE_ME value is a known credential.
        """
        marker = _placeholder_marker(self.bootstrap_password)
        if marker:
            return (
                f"AUTH_BOOTSTRAP_PASSWORD still contains the placeholder text "
                f"{marker!r} — set a real password before first run."
            )
        return None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        if env is None:
            _load_dotenv()  # populate os.environ from .env (real env vars still win)
            e = os.environ
        else:
            e = env  # explicit mapping (tests) — never touches .env
        endpoint = e.get("AWS_ENDPOINT_URL", "").strip() or None
        return cls(
            backend=e.get("BACKEND", "local").strip().lower(),
            storage=e.get("STORAGE", "postgres").strip().lower(),
            database_url=(e.get("DATABASE_URL", "").strip() or None),
            sqlite_path=e.get("SQLITE_PATH", "certwatch.db").strip(),
            aws_region=e.get("AWS_REGION", "us-east-1").strip(),
            aws_endpoint_url=endpoint,
            sns_topic_arn=(e.get("SNS_TOPIC_ARN", "").strip() or None),
            notifier=e.get("NOTIFIER", "console").strip().lower(),
            alert_email=(e.get("ALERT_EMAIL", "").strip() or None),
            alert_recipients=_parse_emails(e.get("ALERT_RECIPIENTS", "")),
            threshold_days=_parse_thresholds(
                e.get("THRESHOLD_DAYS", DEFAULT_THRESHOLD_DAYS)
            ),
            tls_timeout_seconds=float(e.get("TLS_TIMEOUT_SECONDS", "5")),
            agent_stale_after_hours=float(e.get("AGENT_STALE_AFTER_HOURS", "6")),
            agent_transport=e.get("AGENT_TRANSPORT", "ssm").strip().lower(),
            ssm_document_name=e.get("SSM_DOCUMENT_NAME", "AWS-RunShellScript").strip(),
            ssm_poll_interval_seconds=float(e.get("SSM_POLL_INTERVAL_SECONDS", "2")),
            auth_enabled=e.get("AUTH_ENABLED", "true").strip().lower() != "false",
            auth_secret=e.get("AUTH_SECRET", INSECURE_AUTH_SECRET).strip(),
            token_expire_minutes=int(e.get("AUTH_TOKEN_EXPIRE_MINUTES", "720")),
            users_file=e.get("AUTH_USERS_FILE", "auth_users.json").strip(),
            bootstrap_password=e.get("AUTH_BOOTSTRAP_PASSWORD", "admin123"),
            uptime_tick_seconds=_positive(float, e, "UPTIME_TICK_SECONDS", "10"),
            uptime_max_concurrency=_positive(int, e, "UPTIME_MAX_CONCURRENCY", "50"),
            uptime_user_agent=_required(e, "UPTIME_USER_AGENT", "CertWatch-Uptime/1.0"),
            uptime_region=_required(e, "UPTIME_REGION", "default"),
            uptime_max_body_bytes=_positive(int, e, "UPTIME_MAX_BODY_BYTES", "1000000"),
            uptime_failure_threshold=_positive(int, e, "UPTIME_FAILURE_THRESHOLD", "3"),
            uptime_recovery_threshold=_positive(int, e, "UPTIME_RECOVERY_THRESHOLD", "1"),
            uptime_allow_private_targets=(
                e.get("UPTIME_ALLOW_PRIVATE_TARGETS", "false").strip().lower() == "true"
            ),
        )

    def boto_kwargs(self) -> dict:
        """Shared kwargs for every boto3 client/resource.

        ``endpoint_url`` is set only locally (points at LocalStack). On AWS it is
        omitted so the SDK resolves real service endpoints. Dummy credentials are
        injected locally so boto3 never blocks on a missing credential chain.
        """
        kwargs: dict = {"region_name": self.aws_region}
        if self.aws_endpoint_url:
            kwargs["endpoint_url"] = self.aws_endpoint_url
            kwargs["aws_access_key_id"] = os.environ.get("AWS_ACCESS_KEY_ID", "test")
            kwargs["aws_secret_access_key"] = os.environ.get(
                "AWS_SECRET_ACCESS_KEY", "test"
            )
        return kwargs
