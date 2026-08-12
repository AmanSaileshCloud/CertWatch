"""Create the SNS topic and verify the SES sender for alerts.

Idempotent and re-runnable: existing resources are detected and left alone.
Storage is SQLite on the box, so nothing to provision there — this only sets up
the AWS side used by the ``sns_ses`` notifier.

Usage:
    SNS_TOPIC_ARN=arn:aws:sns:us-east-1:ACCOUNT:ssl-alerts \
    ALERT_EMAIL=alerts@yourcompany.com \
    python -m backend.scripts.bootstrap
"""

from __future__ import annotations

import logging
import sys

import boto3
from botocore.exceptions import ClientError

from backend.ssl_monitor.config.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("bootstrap")


def ensure_topic(settings: Settings) -> str:
    sns = boto3.client("sns", **settings.boto_kwargs())
    # create_topic is itself idempotent — returns the existing ARN if present.
    name = (settings.sns_topic_arn or "").rsplit(":", 1)[-1] or "ssl-alerts"
    arn = sns.create_topic(Name=name)["TopicArn"]
    logger.info("SNS topic ready: %s", arn)
    return arn


def ensure_ses_identity(settings: Settings) -> None:
    if not settings.alert_email:
        return
    ses = boto3.client("ses", **settings.boto_kwargs())
    try:
        ses.verify_email_identity(EmailAddress=settings.alert_email)
        logger.info("SES sender identity verification requested: %s", settings.alert_email)
    except ClientError as exc:
        # Non-fatal: real AWS requires out-of-band verification; LocalStack auto-verifies.
        logger.warning("SES verify skipped/failed for %s: %s", settings.alert_email, exc)


def main() -> int:
    settings = Settings.from_env()
    target = settings.aws_endpoint_url or f"AWS ({settings.aws_region})"
    logger.info("Bootstrapping SES/SNS against %s ...", target)
    arn = ensure_topic(settings)
    ensure_ses_identity(settings)
    if settings.sns_topic_arn and settings.sns_topic_arn != arn:
        logger.warning(
            "SNS_TOPIC_ARN in env (%s) differs from created topic (%s); "
            "update your env to match.",
            settings.sns_topic_arn,
            arn,
        )
    logger.info("Bootstrap complete. Confirm the SNS subscription + SES verification emails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
