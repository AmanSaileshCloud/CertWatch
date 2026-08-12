"""Push probe results to CertWatch central API."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime

from probe import ProbeResult

logger = logging.getLogger("agent.reporter")


def push(result: ProbeResult, api_url: str, agent_id: str, api_key: str) -> bool:
    """POST a probe result to the CertWatch API. Returns True on success."""
    payload = {
        "agent_id": agent_id,
        "domain": result.domain,
        "port": result.port,
        "reachable": result.reachable,
        "private": result.private,
        "certificate_expiry": result.not_after.isoformat() if result.not_after else None,
        "days_remaining": result.days_remaining,
        "status": result.status,
        "error": result.error,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url}/agent/report",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Agent-Key": api_key,
            "User-Agent": "CertWatch-Agent/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("reported %s → %s (%s)", result.domain, result.status, resp.status)
            return True
    except urllib.error.HTTPError as e:
        logger.error("API error %s for %s: %s", e.code, result.domain, e.read())
    except urllib.error.URLError as e:
        logger.error("Cannot reach API: %s", e.reason)
    except Exception as e:
        logger.error("Unexpected error reporting %s: %s", result.domain, e)
    return False
