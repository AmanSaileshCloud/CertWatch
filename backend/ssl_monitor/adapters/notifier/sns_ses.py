"""SNS + SES notifier.

SSL alerts: published to an SNS topic (fan-out to any subscribers) and, when an
``ALERT_EMAIL`` is configured, also sent as a direct SES email.
Uptime alerts: SES email only, via ``send_email`` — one SES call per recipient, so
one bad address never blocks the others.

Works against LocalStack and real AWS — only the endpoint differs. Delivery
failures are logged, not raised, so one bad notification never aborts a run.
"""

from __future__ import annotations

import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ...config.settings import Settings
from ...core.alert_models import EmailMessage
from ...core.models import Alert
from .base import format_body, format_html_body, format_subject

logger = logging.getLogger("ssl_monitor.notifier")

# A slow or unreachable AWS endpoint must not stall the uptime worker's checks.
AWS_CLIENT_CONFIG = Config(
    connect_timeout=5, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"}
)


class SnsSesNotifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sns = boto3.client("sns", config=AWS_CLIENT_CONFIG, **settings.boto_kwargs())
        self._ses = boto3.client("ses", config=AWS_CLIENT_CONFIG, **settings.boto_kwargs())

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
            message = EmailMessage(
                subject=subject,
                text=body,
                html=format_html_body(alert),
                to=tuple(recipients),
            )
            self._send_ses(message, list(message.to), context=alert.domain)

    def send_email(self, message: EmailMessage) -> bool:
        """Send to each recipient separately; True if at least one accepted it."""
        if not self._settings.alert_email:
            logger.error("SES send skipped (%s): ALERT_EMAIL (sender) is not set", message.subject)
            return False
        if not message.to:
            logger.error("SES send skipped (%s): no recipients", message.subject)
            return False
        accepted = [
            self._send_ses(message, [recipient], context=f"{message.subject} -> {recipient}")
            for recipient in message.to
        ]
        return any(accepted)

    def _send_ses(self, message: EmailMessage, recipients: list[str], *, context: str) -> bool:
        try:
            self._ses.send_email(
                Source=self._settings.alert_email,
                Destination={"ToAddresses": recipients},
                Message={
                    "Subject": {"Data": message.subject},
                    "Body": {
                        "Html": {"Data": message.html},
                        "Text": {"Data": message.text},
                    },
                },
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error("SES send failed for %s: %s", context, exc)
            return False
        return True
