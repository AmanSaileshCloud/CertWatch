"""Push probe results to CertWatch central API."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone

from probe import ProbeResult

logger = logging.getLogger("agent.reporter")


def push(result: ProbeResult, api_url: str, agent_id: str, api_key: str) -> bool:
    """POST a probe result to the CertWatch API. Returns True on success.

    Sends facts only — ``reachable`` and ``not_after``. The server derives
    days_remaining, status, and alert thresholds from those, so agent and server
    can never disagree about what "expiring" means.
    """
    payload = {
        "agent_id": agent_id,
        "domain": result.domain,
        "port": result.port,
        "reachable": result.reachable,
        "private": result.private,
        "not_after": result.not_after.isoformat() if result.not_after else None,
        "error": result.error,
        "checked_at": datetime.now(timezone.utc).isoformat(),
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
            expiry = result.not_after.date().isoformat() if result.not_after else "-"
            logger.info(
                "reported %s reachable=%s expires=%s (HTTP %s)",
                result.domain, result.reachable, expiry, resp.status,
            )
            return True
    except urllib.error.HTTPError as e:
        logger.error("API error %s for %s: %s", e.code, result.domain, e.read())
    except urllib.error.URLError as e:
        logger.error("Cannot reach API: %s", e.reason)
    except Exception as e:
        logger.error("Unexpected error reporting %s: %s", result.domain, e)
    return False
