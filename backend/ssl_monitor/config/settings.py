"""Environment-driven configuration.

A single :class:`Settings` object is the only place env vars are read. Storage is
SQLite on the box; AWS is used only for SES/SNS alerts (via ``boto_kwargs``).
Core logic never imports this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_THRESHOLD_DAYS = "30,14,7,1"

# The built-in placeholder secret. Refused at startup when auth is enabled.
INSECURE_AUTH_SECRET = "dev-insecure-change-me-please-set-a-real-AUTH_SECRET"


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


def _parse_thresholds(raw: str) -> list[int]:
    """Parse ``"30,14,7,1"`` → ``[30, 14, 7, 1]`` (deduped, descending)."""
    values = {int(part.strip()) for part in raw.split(",") if part.strip()}
    if not values:
        raise ValueError("THRESHOLD_DAYS must contain at least one integer")
    return sorted(values, reverse=True)


@dataclass(frozen=True)
class Settings:
    backend: str  # "local" | "aws"
    storage: str  # "sqlite" (default) | "memory" (non-persistent dev only)
    sqlite_path: str  # DB file path when storage=sqlite
    aws_region: str  # for SES/SNS
    aws_endpoint_url: str | None  # LocalStack URL locally; None on real AWS
    sns_topic_arn: str | None
    notifier: str  # "console" | "sns_ses"
    alert_email: str | None
    threshold_days: list[int]
    tls_timeout_seconds: float
    # Auth — username/password JWT (self-contained, no external service).
    auth_enabled: bool
    auth_secret: str
    token_expire_minutes: int
    users_file: str

    @property
    def is_local(self) -> bool:
        return self.backend == "local"

    @property
    def auth_secret_is_insecure(self) -> bool:
        return self.auth_secret == INSECURE_AUTH_SECRET

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
            storage=e.get("STORAGE", "sqlite").strip().lower(),
            sqlite_path=e.get("SQLITE_PATH", "certwatch.db").strip(),
            aws_region=e.get("AWS_REGION", "us-east-1").strip(),
            aws_endpoint_url=endpoint,
            sns_topic_arn=(e.get("SNS_TOPIC_ARN", "").strip() or None),
            notifier=e.get("NOTIFIER", "console").strip().lower(),
            alert_email=(e.get("ALERT_EMAIL", "").strip() or None),
            threshold_days=_parse_thresholds(
                e.get("THRESHOLD_DAYS", DEFAULT_THRESHOLD_DAYS)
            ),
            tls_timeout_seconds=float(e.get("TLS_TIMEOUT_SECONDS", "5")),
            auth_enabled=e.get("AUTH_ENABLED", "true").strip().lower() != "false",
            auth_secret=e.get("AUTH_SECRET", INSECURE_AUTH_SECRET).strip(),
            token_expire_minutes=int(e.get("AUTH_TOKEN_EXPIRE_MINUTES", "720")),
            users_file=e.get("AUTH_USERS_FILE", "auth_users.json").strip(),
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
