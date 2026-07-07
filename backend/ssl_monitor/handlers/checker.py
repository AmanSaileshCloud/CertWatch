"""Lambda-style checker handler.

Thin wrapper: build adapters from config, stamp the clock, delegate to the
``run_check`` service, return a JSON-serializable summary. This is what an
EventBridge schedule invokes in AWS; locally the runner calls it too.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..services.checker import run_check
from ._deps import get_settings, make_notifier, make_storage

logger = logging.getLogger("ssl_monitor.checker")


def handler(event: dict | None = None, context: object = None) -> dict:
    """EventBridge/manual entrypoint. ``event`` is currently unused."""
    settings = get_settings()
    storage = make_storage(settings)
    notifier = make_notifier(settings)
    now = datetime.now(timezone.utc)

    summary = run_check(
        storage,
        notifier,
        thresholds=settings.threshold_days,
        timeout=settings.tls_timeout_seconds,
        now=now,
    )
    logger.info(
        "checker run: checked=%s alerts=%s by_status=%s",
        summary["checked"],
        summary["alerts_sent"],
        summary["by_status"],
    )
    return summary
