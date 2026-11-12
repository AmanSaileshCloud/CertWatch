"""Amazon SES over its SMTP interface, with the username/password saved on the
Settings page (an alternative to the IAM-role SES API in ``sns_ses``).

* Ports 587 / 25 / 2587 use STARTTLS; 465 / 2465 use implicit TLS. Timeout 10 s.
* One message per recipient, so one refused address never blocks the others;
  ``send_email`` is True when at least one recipient was accepted.
* SSL expiry alerts (``notify``) go by email only — SNS is not used in SMTP mode.
* Delivery problems are logged and never raised. The password is never logged.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable
from typing import Any

from ...core.alert_models import EmailMessage
from ...core.app_settings_models import AlertConfig
from ...core.models import Alert
from .base import format_body, format_html_body, format_subject
from .mime import build_mime

logger = logging.getLogger("ssl_monitor.notifier")

SMTP_TIMEOUT_SECONDS = 10
IMPLICIT_TLS_PORTS = frozenset({465, 2465})

SmtpFactory = Callable[..., Any]


class SesSmtpNotifier:
    def __init__(
        self,
        config: AlertConfig,
        *,
        smtp_factory: SmtpFactory = smtplib.SMTP,
        smtp_ssl_factory: SmtpFactory = smtplib.SMTP_SSL,
    ) -> None:
        self._config = config
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory
        self.last_error: str | None = None  # safe to show an admin (no secrets)

    def notify(self, alert: Alert) -> None:
        sender = self._config.alert_email
        if not sender:
            logger.error("SES SMTP alert skipped for %s: no sender address", alert.domain)
            return
        recipients = dict.fromkeys([sender, *self._config.alert_recipients])
        self.send_email(EmailMessage(
            subject=format_subject(alert),
            text=format_body(alert),
            html=format_html_body(alert, self._config.report_timezone),
            to=tuple(recipients),
        ))

    def send_email(self, message: EmailMessage) -> bool:
        self.last_error = self._missing_setting(message)
        if self.last_error:
            logger.error("SES SMTP send skipped (%s): %s", message.subject, self.last_error)
            return False
        try:
            return self._deliver(message)
        except Exception as exc:  # noqa: BLE001 - delivery must never abort the caller
            self.last_error = self._describe(exc)
            logger.error("SES SMTP send failed (%s) via %s:%s: %s", message.subject,
                         self._config.smtp_host, self._config.smtp_port, self.last_error)
            return False

    def _missing_setting(self, message: EmailMessage) -> str | None:
        config = self._config
        required = {
            "sender address": config.alert_email,
            "SMTP host": config.smtp_host,
            "SMTP username": config.smtp_username,
            "SMTP password": config.smtp_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            return f"{', '.join(missing)} not set"
        return None if message.to else "no recipients"

    def _deliver(self, message: EmailMessage) -> bool:
        with self._connect() as smtp:
            self._start_session(smtp)
            accepted = [self._send_one(smtp, message, to) for to in message.to]
        return any(accepted)

    def _connect(self) -> Any:
        host, port = self._config.smtp_host, self._config.smtp_port
        if port in IMPLICIT_TLS_PORTS:
            return self._smtp_ssl_factory(host, port, timeout=SMTP_TIMEOUT_SECONDS,
                                          context=ssl.create_default_context())
        return self._smtp_factory(host, port, timeout=SMTP_TIMEOUT_SECONDS)

    def _start_session(self, smtp: Any) -> None:
        if self._config.smtp_port not in IMPLICIT_TLS_PORTS:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(self._config.smtp_username, self._config.smtp_password)

    def _send_one(self, smtp: Any, message: EmailMessage, to: str) -> bool:
        sender = self._config.alert_email
        try:
            smtp.sendmail(sender, [to], build_mime(message, sender, to).as_string())
        except smtplib.SMTPException as exc:
            self.last_error = self._describe(exc)
            logger.error("SES SMTP refused %s (%s): %s", to, message.subject, self.last_error)
            return False
        return True

    def _describe(self, exc: Exception) -> str:
        """The provider's reply (e.g. "535 Authentication Credentials Invalid"), with
        the password masked in case a server ever echoes it."""
        if isinstance(exc, smtplib.SMTPResponseException):
            reply = exc.smtp_error
            if isinstance(reply, bytes):
                reply = reply.decode(errors="replace")
            text = f"{exc.smtp_code} {reply}"
        else:
            text = f"{type(exc).__name__}: {exc}"
        password = self._config.smtp_password
        return text.replace(password, "***") if password else text
