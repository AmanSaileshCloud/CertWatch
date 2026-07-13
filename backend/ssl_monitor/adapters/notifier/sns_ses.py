"""SNS + SES notifier.

Publishes the alert to an SNS topic (fan-out to any subscribers) and, when an
``ALERT_EMAIL`` is configured, also sends a direct SES email. Works against
LocalStack and real AWS — only the endpoint differs. Delivery failures are
logged, not raised, so one bad notification never aborts a checker run.
"""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

from ...config.settings import Settings
from ...core.models import Alert
from .base import format_body, format_html_body, format_subject

logger = logging.getLogger("ssl_monitor.notifier")


class SnsSesNotifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sns = boto3.client("sns", **settings.boto_kwargs())
        self._ses = boto3.client("ses", **settings.boto_kwargs())

    def notify(self, alert: Alert) -> None:
        subject = format_subject(alert)
        body = format_body(alert)

        # SNS is plain-text only (AWS limit) — used for fan-out to other systems.
        if self._settings.sns_topic_arn:
            try:
                self._sns.publish(
                    TopicArn=self._settings.sns_topic_arn,
                    Subject=subject,
                    Message=body,
                )
            except ClientError as exc:
                logger.error("SNS publish failed for %s: %s", alert.domain, exc)

        # SES carries the rich HTML card (with the plain text as a fallback part).
        # Sent to ALERT_EMAIL plus any extra ALERT_RECIPIENTS (deduped).
        if self._settings.alert_email:
            recipients = [self._settings.alert_email] + [
                r for r in self._settings.alert_recipients
                if r != self._settings.alert_email
            ]
            try:
                self._ses.send_email(
                    Source=self._settings.alert_email,
                    Destination={"ToAddresses": recipients},
                    Message={
                        "Subject": {"Data": subject},
                        "Body": {
                            "Html": {"Data": format_html_body(alert)},
                            "Text": {"Data": body},
                        },
                    },
                )
            except ClientError as exc:
                logger.error("SES send failed for %s: %s", alert.domain, exc)
