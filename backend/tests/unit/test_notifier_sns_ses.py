"""Unit tests for the SNS + SES notifier, with fake boto3 clients (no network)."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend.ssl_monitor.adapters.notifier import sns_ses
from backend.ssl_monitor.adapters.notifier.base import (
    NotifierPort,
    format_body,
    format_html_body,
    format_subject,
)
from backend.ssl_monitor.adapters.notifier.sns_ses import SnsSesNotifier
from backend.ssl_monitor.config.settings import Settings
from backend.ssl_monitor.core.alert_models import EmailMessage
from backend.ssl_monitor.core.models import Alert, Status

SENDER = "alerts@example.com"
TOPIC = "arn:aws:sns:us-east-1:000000000000:certwatch"
MESSAGE = EmailMessage(
    subject="[DOWN] Shop", text="plain", html="<p>html</p>", to=("ops@example.com",)
)


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._error = error
        self.refused: dict[str, Exception] = {}  # recipient -> error raised for it
        self.config = None  # the botocore Config the client was created with

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))
        if self._error:
            raise self._error
        for recipient in kwargs.get("Destination", {}).get("ToAddresses", []):
            if recipient in self.refused:
                raise self.refused[recipient]

    def publish(self, **kwargs):
        self._record("publish", kwargs)

    def send_email(self, **kwargs):
        self._record("send_email", kwargs)


@pytest.fixture
def clients(monkeypatch) -> dict[str, FakeClient]:
    fakes = {"sns": FakeClient(), "ses": FakeClient()}

    def fake_client(service, *, config, **_):
        fakes[service].config = config
        return fakes[service]

    monkeypatch.setattr(sns_ses.boto3, "client", fake_client)
    return fakes


def _settings(**overrides) -> Settings:
    base = Settings.from_env(env={
        "ALERT_EMAIL": SENDER,
        "ALERT_RECIPIENTS": "team@example.com",
        "SNS_TOPIC_ARN": TOPIC,
    })
    return dataclasses.replace(base, **overrides)


def _client_error() -> ClientError:
    return ClientError({"Error": {"Code": "MessageRejected", "Message": "nope"}}, "SendEmail")


def test_satisfies_port(clients):
    assert isinstance(SnsSesNotifier(_settings()), NotifierPort)


def test_send_email_success(clients):
    assert SnsSesNotifier(_settings()).send_email(MESSAGE) is True
    assert clients["ses"].calls == [("send_email", {
        "Source": SENDER,
        "Destination": {"ToAddresses": ["ops@example.com"]},
        "Message": {
            "Subject": {"Data": "[DOWN] Shop"},
            "Body": {"Html": {"Data": "<p>html</p>"}, "Text": {"Data": "plain"}},
        },
    })]
    assert clients["sns"].calls == []  # uptime alerts are SES only


@pytest.mark.parametrize(
    "error", [_client_error(), EndpointConnectionError(endpoint_url="http://x")]
)
def test_send_email_failure_returns_false(clients, error, caplog):
    clients["ses"]._error = error
    assert SnsSesNotifier(_settings()).send_email(MESSAGE) is False
    assert "SES send failed" in caplog.text


def test_one_message_per_recipient_and_a_bad_one_does_not_block_the_rest(clients, caplog):
    clients["ses"].refused["bad@example.com"] = _client_error()
    message = dataclasses.replace(MESSAGE, to=("bad@example.com", "ops@example.com"))

    assert SnsSesNotifier(_settings()).send_email(message) is True
    assert [c[1]["Destination"]["ToAddresses"] for c in clients["ses"].calls] == [
        ["bad@example.com"], ["ops@example.com"]]
    assert "bad@example.com" in caplog.text


def test_botocore_error_for_every_recipient_returns_false(clients, caplog):
    error = EndpointConnectionError(endpoint_url="http://x")
    clients["ses"].refused.update({"a@example.com": error, "b@example.com": error})
    message = dataclasses.replace(MESSAGE, to=("a@example.com", "b@example.com"))

    assert SnsSesNotifier(_settings()).send_email(message) is False
    assert len(clients["ses"].calls) == 2
    assert "SES send failed for [DOWN] Shop -> b@example.com" in caplog.text


def test_clients_have_short_timeouts_and_few_retries(clients):
    SnsSesNotifier(_settings())
    for config in (clients["ses"].config, clients["sns"].config):
        assert (config.connect_timeout, config.read_timeout) == (5, 10)
        assert config.retries == {"max_attempts": 2, "mode": "standard"}


def test_send_email_without_sender_skips_ses(clients):
    assert SnsSesNotifier(_settings(alert_email=None)).send_email(MESSAGE) is False
    assert clients["ses"].calls == []


def test_send_email_without_recipients_skips_ses(clients):
    message = dataclasses.replace(MESSAGE, to=())
    assert SnsSesNotifier(_settings()).send_email(message) is False
    assert clients["ses"].calls == []


def _alert() -> Alert:
    return Alert(
        domain="example.com:443",
        status=Status.WARNING,
        days_remaining=6,
        threshold=7,
        not_after=dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.timezone.utc),
    )


def test_ssl_notify_publishes_sns_and_sends_ses_unchanged(clients, monkeypatch):
    monkeypatch.setattr(sns_ses, "format_html_body", lambda alert, tz="UTC": "<card>")
    alert = _alert()
    SnsSesNotifier(_settings()).notify(alert)

    assert clients["sns"].calls == [("publish", {
        "TopicArn": TOPIC, "Subject": format_subject(alert), "Message": format_body(alert),
    })]
    assert clients["ses"].calls == [("send_email", {
        "Source": SENDER,
        "Destination": {"ToAddresses": [SENDER, "team@example.com"]},
        "Message": {
            "Subject": {"Data": "[SSL WARNING] example.com:443"},
            "Body": {"Html": {"Data": "<card>"}, "Text": {"Data": format_body(alert)}},
        },
    })]


def test_ssl_notify_html_is_the_ssl_card(clients):
    SnsSesNotifier(_settings()).notify(_alert())
    html = clients["ses"].calls[0][1]["Message"]["Body"]["Html"]["Data"]
    assert html.startswith(format_html_body(_alert())[:200])


def test_ssl_notify_swallows_errors(clients):
    clients["sns"]._error = _client_error()
    clients["ses"]._error = _client_error()
    SnsSesNotifier(_settings()).notify(_alert())  # must not raise
    assert len(clients["ses"].calls) == 1


def test_message_with_images_goes_as_raw_email(clients):
    from email import message_from_bytes

    from backend.ssl_monitor.core.alert_models import InlineImage

    message = dataclasses.replace(MESSAGE, images=(InlineImage("response-chart", b"\x89PNGdata"),))
    clients["ses"].send_raw_email = lambda **kw: clients["ses"]._record("send_raw_email", kw)
    assert SnsSesNotifier(_settings()).send_email(message) is True
    [(name, kwargs)] = clients["ses"].calls
    assert name == "send_raw_email"
    assert kwargs["Source"] == SENDER
    assert kwargs["Destinations"] == ["ops@example.com"]
    mime = message_from_bytes(kwargs["RawMessage"]["Data"])
    assert mime["To"] == "ops@example.com"
    [image] = [p for p in mime.walk() if p.get_content_type() == "image/png"]
    assert image["Content-ID"] == "<response-chart>"
    assert image.get_payload(decode=True) == b"\x89PNGdata"
