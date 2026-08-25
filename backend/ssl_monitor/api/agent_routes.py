"""Agent reporting endpoint — receives SSL probe results from remote agents.

Agents run inside private networks and push facts outbound: was the host
reachable, and what is the certificate's ``notAfter``. Everything derived from
that — days remaining, status, alert thresholds — is computed *here*, using the
same core functions the direct checker uses. The agent deliberately does not
classify, so the two can never disagree about what "expiring" means.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..adapters.storage.base import StoragePort
from ..config.settings import Settings
from ..core.models import SOURCE_AGENT, DomainRecord, Status, make_domain_key
from ..core.status import classify_status, days_remaining
from .dependencies import get_app_settings, get_storage

logger = logging.getLogger("ssl_monitor.agent")

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentReport(BaseModel):
    """One host's probe result, as pushed by an agent.

    ``status``/``days_remaining``/``certificate_expiry`` are legacy fields from
    an older agent build. They are accepted so an un-upgraded agent doesn't get
    a 422, but only ``certificate_expiry`` is used (as a fallback for
    ``not_after``) — a client-supplied status is never trusted.
    """

    agent_id: str
    domain: str
    port: int = 443
    reachable: bool
    private: bool = False
    not_after: str | None = None
    error: str | None = None
    checked_at: str | None = None
    # Legacy, accepted-but-ignored.
    certificate_expiry: str | None = None
    days_remaining: int | None = None
    status: str | None = None

    def expiry(self) -> str | None:
        return self.not_after or self.certificate_expiry


def _verify_key(supplied: str | None) -> None:
    """Constant-time check of the shared agent key."""
    expected = os.environ.get("AGENT_API_KEY", "")
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_API_KEY not configured on server",
        )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid agent key")


def _parse_expiry(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Storage and the core helpers require UTC-aware datetimes.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.post("/report", status_code=status.HTTP_200_OK)
def receive_report(
    body: AgentReport,
    x_agent_key: str | None = Header(default=None),
    storage: StoragePort = Depends(get_storage),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    _verify_key(x_agent_key)

    now = datetime.now(timezone.utc)
    domain_key = make_domain_key(body.domain, body.port)
    record = storage.get(domain_key)
    if record is None:
        record = DomainRecord.new(host=body.domain, port=body.port, created_at=now)

    # Claim the record for the agent: the central checker must never probe it.
    if not record.is_agent_managed:
        logger.info("domain %s is now agent-managed (agent=%s)", domain_key, body.agent_id)
    record.source = SOURCE_AGENT
    record.agent_id = body.agent_id
    record.stale_alert_sent = False  # a report arrived — agent is alive

    not_after = _parse_expiry(body.expiry())

    if body.reachable and not_after is not None:
        record.not_after = not_after
        record.days_remaining = days_remaining(not_after, now)
        record.last_error = None
    else:
        # The agent reached us but could not read a cert. Record why; never
        # invent an expiry, and never let this look like an expiring cert.
        record.not_after = None
        record.days_remaining = None
        record.last_error = body.error or (
            "agent reported no certificate" if body.reachable else "agent could not reach host"
        )

    # Server-side classification, using the operator's configured thresholds.
    record.status = classify_status(
        body.reachable and not_after is not None,
        record.days_remaining,
        settings.threshold_days,
    )
    record.last_checked_at = now
    storage.put(record)

    logger.info(
        "agent=%s domain=%s reachable=%s status=%s days=%s",
        body.agent_id, domain_key, body.reachable, record.status.value, record.days_remaining,
    )
    return {
        "ok": True,
        "domain": domain_key,
        "status": record.status.value,
        "days_remaining": record.days_remaining,
    }


def get_agent_router(_storage: StoragePort | None = None) -> APIRouter:
    """Return the agent router.

    The argument is ignored and kept only for backwards compatibility with the
    old call site: storage now arrives per-request via ``Depends(get_storage)``,
    so the router is no longer bound to an adapter built at import time (which
    made this endpoint impossible to override in tests).
    """
    return router
