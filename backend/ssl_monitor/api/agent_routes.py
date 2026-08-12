"""Agent reporting endpoint — receives SSL probe results from remote agents."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from ..adapters.storage.base import StoragePort
from ..core.models import DomainRecord, Status, make_domain_key

logger = logging.getLogger("ssl_monitor.agent")

router = APIRouter(prefix="/agent", tags=["agent"])

_AGENT_KEY = os.environ.get("AGENT_API_KEY", "")


class AgentReport(BaseModel):
    agent_id: str
    domain: str
    port: int = 443
    reachable: bool
    private: bool = False
    certificate_expiry: str | None = None
    days_remaining: int | None = None
    status: str = "unreachable"
    error: str | None = None
    checked_at: str | None = None


def _verify_key(x_agent_key: str | None) -> None:
    if not _AGENT_KEY:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGENT_API_KEY not configured on server",
        )
    if x_agent_key != _AGENT_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid agent key")


def get_agent_router(storage: StoragePort) -> APIRouter:

    @router.post("/report", status_code=status.HTTP_200_OK)
    def receive_report(
        body: AgentReport,
        x_agent_key: str | None = Header(default=None),
    ) -> dict:
        _verify_key(x_agent_key)

        domain_key = make_domain_key(body.domain, body.port)
        record = storage.get(domain_key)

        if record is None:
            record = DomainRecord.new(
                host=body.domain,
                port=body.port,
                created_at=datetime.now(timezone.utc),
            )

        record.status = Status(body.status) if body.status in Status._value2member_map_ \
            else Status.UNREACHABLE
        record.days_remaining = body.days_remaining
        record.last_error = body.error
        record.last_checked_at = datetime.now(timezone.utc)

        if body.certificate_expiry:
            try:
                record.not_after = datetime.fromisoformat(
                    body.certificate_expiry.replace("Z", "+00:00")
                )
            except ValueError:
                pass

        storage.put(record)

        logger.info(
            "agent=%s domain=%s status=%s days=%s",
            body.agent_id, domain_key, body.status, body.days_remaining,
        )
        return {"ok": True, "domain": domain_key}

    return router
