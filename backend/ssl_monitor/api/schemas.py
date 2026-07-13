"""Request/response models for the API (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..core.models import DomainRecord, Status


class DomainIn(BaseModel):
    domain: str = Field(..., description="Bare hostname to monitor, e.g. example.com")
    port: int = Field(default=443, ge=1, le=65535)


class DomainOut(BaseModel):
    domain: str
    host: str
    port: int
    status: Status
    not_after: datetime | None
    days_remaining: int | None
    last_checked_at: datetime | None
    last_error: str | None
    last_alert_threshold: int | None
    created_at: datetime
    alerts_enabled: bool

    @classmethod
    def from_record(cls, r: DomainRecord) -> "DomainOut":
        return cls(
            domain=r.domain,
            host=r.host,
            port=r.port,
            status=r.status,
            not_after=r.not_after,
            days_remaining=r.days_remaining,
            last_checked_at=r.last_checked_at,
            last_error=r.last_error,
            last_alert_threshold=r.last_alert_threshold,
            created_at=r.created_at,
            alerts_enabled=r.alerts_enabled,
        )


class DomainUpdateIn(BaseModel):
    alerts_enabled: bool | None = None


class CheckSummary(BaseModel):
    checked_at: str
    checked: int
    alerts_sent: int
    by_status: dict[str, int]
    domains: list[dict]


class ErrorOut(BaseModel):
    error: str


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    """Identity + role of the authenticated user (for /auth/me)."""

    username: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AdminUserIn(BaseModel):
    username: str
    password: str
    role: str = Field(default="user", pattern="^(user|admin)$")


class AdminUserOut(BaseModel):
    username: str
    role: str


class MessageOut(BaseModel):
    status: str = "ok"
    message: str


class BulkDomainIn(BaseModel):
    domains: list[DomainIn]


class BulkDomainResult(BaseModel):
    added: list[DomainOut]
    failed: list[dict]


class CertOut(BaseModel):
    """Full leaf-certificate details (fetched on demand for the detail view)."""

    issuer: str
    subject: str
    serial: str
    sig_algorithm: str
    key_type: str
    key_bits: int | None
    not_before: datetime | None
    not_after: datetime | None
    sans: list[str]


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
