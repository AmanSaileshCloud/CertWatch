"""Local scheduler / runner for the SSL checker.

Runs continuously as the certwatch-checker-loop systemd service. Three modes:

  --check HOST[:PORT] ...   Ad-hoc probe of one or more hosts. No storage —
                            uses the console notifier. Great for a quick
                            demo:  python -m backend.runner.run_checker --check google.com
  --once                    Run the checker over all *stored* domains once.
                            Reads the SQLite store.
  --interval N              Repeat --once every N seconds until Ctrl-C.

Examples:
  python -m backend.runner.run_checker --check google.com --check expired.badssl.com
  python -m backend.runner.run_checker --once
  python -m backend.runner.run_checker --interval 300
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from ..ssl_monitor.adapters.notifier.console import ConsoleNotifier
from ..ssl_monitor.config.settings import Settings
from ..ssl_monitor.core.models import DomainRecord, make_domain_key
from ..ssl_monitor.handlers.checker import handler as checker_handler
from ..ssl_monitor.services.checker import CheckOutcome, check_one, summarize

logger = logging.getLogger("ssl_monitor.runner")


def _parse_target(value: str) -> tuple[str, int]:
    """Parse ``host`` or ``host:port`` → (host, port). Defaults to 443."""
    if ":" in value:
        host, _, port = value.rpartition(":")
        return host, int(port)
    return value, 443


def _print_summary(summary: dict) -> None:
    print(f"\nChecked {summary['checked']} domain(s) at {summary['checked_at']}")
    print(f"Alerts sent: {summary['alerts_sent']}   By status: {summary['by_status']}")
    print("-" * 78)
    print(f"{'DOMAIN':<34}{'STATUS':<13}{'DAYS':>6}  {'EXPIRES / ERROR'}")
    print("-" * 78)
    for d in summary["domains"]:
        days = "-" if d["days_remaining"] is None else str(d["days_remaining"])
        detail = d["not_after"] or d["error"] or ""
        flag = "  <-- ALERT" if d["alerted"] else ""
        print(f"{d['domain']:<34}{d['status']:<13}{days:>6}  {detail}{flag}")
    print("-" * 78)


def run_adhoc(targets: list[str], settings: Settings) -> dict:
    """Probe given hosts directly, with no storage. Console notifier only."""
    notifier = ConsoleNotifier()
    now = datetime.now(timezone.utc)
    outcomes: list[CheckOutcome] = []

    for raw in targets:
        host, port = _parse_target(raw)
        record = DomainRecord.new(host=host, port=port, created_at=now)
        record.domain = make_domain_key(host, port)
        updated, decision = check_one(
            record,
            now=now,
            thresholds=settings.threshold_days,
            timeout=settings.tls_timeout_seconds,
        )
        if decision.should_alert:
            from ..ssl_monitor.core.models import Alert

            notifier.notify(
                Alert(
                    domain=updated.domain,
                    status=updated.status,
                    days_remaining=updated.days_remaining,
                    threshold=decision.threshold,
                    not_after=updated.not_after,
                )
            )
        outcomes.append(CheckOutcome(updated, decision.should_alert, decision.threshold))

    return summarize(outcomes, now)


def run_once() -> dict:
    """Run the storage-backed checker handler once."""
    return checker_handler({}, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SSL expiry checker runner")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="append",
        metavar="HOST[:PORT]",
        help="ad-hoc probe (no storage/AWS); repeatable",
    )
    mode.add_argument("--once", action="store_true", help="check all stored domains once")
    mode.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="check all stored domains every N seconds",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    settings = Settings.from_env()

    if args.check:
        _print_summary(run_adhoc(args.check, settings))
        return 0

    if args.once:
        try:
            _print_summary(run_once())
        except Exception as exc:  # noqa: BLE001
            logger.error("checker run failed (is the storage backend up?): %s", exc)
            return 1
        return 0

    # --interval
    interval = args.interval
    logger.info("Starting interval runner: every %ss (Ctrl-C to stop)", interval)
    try:
        while True:
            try:
                _print_summary(run_once())
            except Exception as exc:  # noqa: BLE001
                logger.error("checker run failed: %s", exc)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
