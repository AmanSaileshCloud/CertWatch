"""SesSmtpNotifier against a fake SMTP server (no network)."""

from __future__ import annotations

import dataclasses
import email
import logging
import smtplib
from dataclasses import dataclass, field

import pytest

from backend.ssl_monitor.adapters.notifier.base import NotifierPort, format_subject
from backend.ssl_monitor.adapters.notifier.ses_smtp import SMTP_TIMEOUT_SECONDS, SesSmtpNotifier
from backend.ssl_monitor.core.alert_models import EmailMessage
from backend.ssl_monitor.core.app_settings_models import AlertConfig, EmailTransport
from backend.ssl_monitor.core.models import Alert, Status

PASSWORD = "BPa55-w0rd/very+secret"
CONFIG = AlertConfig(
    alert_email="alerts@x.com", alert_recipients=["noc@x.com", "alerts@x.com"],
    uptime_reminder_minutes=30, uptime_alert_delay_minutes=0, public_url=None,
    transport=EmailTransport.SMTP, aws_region="ap-south-1",
    smtp_host="email-smtp.ap-south-1.amazonaws.com", smtp_port=587,
    smtp_username="AKIAEXAMPLE", smtp_password=PASSWORD,
)
MESSAGE = EmailMessage(subject="[DOWN] Shop", text="plain body", html="<p>html body</p>",
                       to=("a@x.com", "b@x.com"))


@dataclass
class FakeSession:
    server: FakeServer
    host: str
    port: int
    timeout: float
    started_tls: bool = False
    login_args: tuple | None = None
    sent: list[tuple[str, list[str], str]] = field(default_factory=list)
    closed: bool = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)
        if self.server.login_error:
            raise self.server.login_error

    def sendmail(self, sender, to, raw):
        if self.server.send_error:
            raise self.server.send_error
        if to[0] in self.server.refuse:
            raise smtplib.SMTPRecipientsRefused({to[0]: (550, b"Mailbox unavailable")})
        self.sent.append((sender, to, raw))


@dataclass
class FakeServer:
    refuse: set[str] = field(default_factory=set)
    login_error: Exception | None = None
    send_error: Exception | None = None
    connect_error: Exception | None = None
    sessions: list[FakeSession] = field(default_factory=list)

    def __call__(self, host, port, *, timeout, context=None):
        if self.connect_error:
            raise self.connect_error
        session = FakeSession(self, host, port, timeout)
        self.sessions.append(session)
        return session


@dataclass
class Rig:
    plain: FakeServer
    tls: FakeServer

    def notifier(self, **changes) -> SesSmtpNotifier:
        config = dataclasses.replace(CONFIG, **changes)
        return SesSmtpNotifier(config, smtp_factory=self.plain, smtp_ssl_factory=self.tls)

    @property
    def session(self) -> FakeSession:
        (session,) = self.plain.sessions + self.tls.sessions
        return session


@pytest.fixture
def rig() -> Rig:
    return Rig(FakeServer(), FakeServer())


def test_satisfies_port(rig):
    assert isinstance(rig.notifier(), NotifierPort)


@pytest.mark.parametrize("port", [587, 25, 2587])
def test_starttls_ports(rig, port):
    assert rig.notifier(smtp_port=port).send_email(MESSAGE) is True
    session = rig.plain.sessions[0]
    assert not rig.tls.sessions
    assert session.started_tls
    assert (session.host, session.port, session.timeout) == (
        CONFIG.smtp_host, port, SMTP_TIMEOUT_SECONDS)


@pytest.mark.parametrize("port", [465, 2465])
def test_implicit_tls_ports(rig, port):
    assert rig.notifier(smtp_port=port).send_email(MESSAGE) is True
    assert not rig.plain.sessions
    assert not rig.tls.sessions[0].started_tls


def test_logs_in_and_sends_one_message_per_recipient(rig):
    assert rig.notifier().send_email(MESSAGE) is True
    session = rig.session
    assert session.login_args == ("AKIAEXAMPLE", PASSWORD)
    assert [(sender, to) for sender, to, _ in session.sent] == [
        ("alerts@x.com", ["a@x.com"]), ("alerts@x.com", ["b@x.com"])]
    assert session.closed


def test_message_has_text_and_html_parts(rig):
    rig.notifier().send_email(MESSAGE)
    mime = email.message_from_string(rig.session.sent[0][2])
    assert (mime["Subject"], mime["From"], mime["To"]) == ("[DOWN] Shop", "alerts@x.com", "a@x.com")
    types = [part.get_content_type() for part in mime.walk()]
    assert "text/plain" in types and "text/html" in types


def test_partial_failure_is_still_sent(rig):
    rig.plain.refuse = {"a@x.com"}
    notifier = rig.notifier()
    assert notifier.send_email(MESSAGE) is True
    assert [to for _, to, _ in rig.session.sent] == [["b@x.com"]]


def test_all_refused_is_not_sent(rig):
    rig.plain.refuse = {"a@x.com", "b@x.com"}
    assert rig.notifier().send_email(MESSAGE) is False


@pytest.mark.parametrize("error", [
    smtplib.SMTPAuthenticationError(535, b"Authentication Credentials Invalid"),
    OSError("connection refused"),
    RuntimeError("unexpected"),
])
def test_errors_never_escape(rig, error):
    rig.plain.login_error = error
    assert rig.notifier().send_email(MESSAGE) is False
    assert rig.session.closed


def test_connect_failure_is_not_sent(rig):
    rig.plain.connect_error = TimeoutError("timed out")
    notifier = rig.notifier()
    assert notifier.send_email(MESSAGE) is False
    assert "timed out" in notifier.last_error


def test_unexpected_send_error_is_not_sent(rig):
    rig.plain.send_error = RuntimeError("boom")
    assert rig.notifier().send_email(MESSAGE) is False


def test_provider_reply_is_kept_without_the_password(rig, caplog):
    rig.plain.login_error = smtplib.SMTPAuthenticationError(
        535, f"Authentication Credentials Invalid for {PASSWORD}".encode())
    notifier = rig.notifier()
    with caplog.at_level(logging.DEBUG):
        notifier.send_email(MESSAGE)
    assert notifier.last_error.startswith("535 Authentication Credentials Invalid")
    assert PASSWORD not in notifier.last_error
    assert PASSWORD not in caplog.text
    assert "AKIAEXAMPLE" not in caplog.text


@pytest.mark.parametrize("missing", ["alert_email", "smtp_host", "smtp_username",
                                     "smtp_password"])
def test_incomplete_config_does_not_connect(rig, missing, caplog):
    notifier = rig.notifier(**{missing: None})
    assert notifier.send_email(MESSAGE) is False
    assert not rig.plain.sessions
    assert notifier.last_error.endswith("not set")


def test_no_recipients_does_not_connect(rig):
    assert rig.notifier().send_email(dataclasses.replace(MESSAGE, to=())) is False
    assert not rig.plain.sessions


def test_ssl_alert_goes_to_sender_and_recipients_by_email(rig):
    alert = Alert(domain="shop.x.com", status=Status.WARNING, days_remaining=5, threshold=7)
    rig.notifier().notify(alert)
    sent = rig.session.sent
    assert [to for _, to, _ in sent] == [["alerts@x.com"], ["noc@x.com"]]
    assert email.message_from_string(sent[0][2])["Subject"] == format_subject(alert)


def test_ssl_alert_without_sender_is_skipped(rig):
    alert = Alert(domain="shop.x.com", status=Status.WARNING, days_remaining=5, threshold=7)
    rig.notifier(alert_email=None).notify(alert)
    assert not rig.plain.sessions
