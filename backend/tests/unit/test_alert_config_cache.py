"""services.alert_config_cache: reload rules for the cached alert configuration."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.ssl_monitor.core.app_settings_models import AlertConfig, EmailTransport
from backend.ssl_monitor.services.alert_config_cache import AlertConfigCache, BuiltFor

CONFIG = AlertConfig(alert_email="ops@acme.test", alert_recipients=["ops@acme.test"],
                     uptime_reminder_minutes=30, uptime_alert_delay_minutes=0,
                     public_url=None, transport=EmailTransport.IAM_ROLE, aws_region="ap-south-1")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Loader:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> AlertConfig:
        self.calls += 1
        return replace(CONFIG, uptime_reminder_minutes=self.calls)


def test_reuses_the_config_until_it_is_too_old() -> None:
    clock, load = Clock(), Loader()
    cache = AlertConfigCache(ttl_seconds=30, clock=clock)
    first = cache.get(load)
    clock.now += 29
    assert cache.get(load) is first
    clock.now += 1
    assert cache.get(load).uptime_reminder_minutes == 2
    assert load.calls == 2


def test_invalidate_forces_a_fresh_read() -> None:
    load = Loader()
    cache = AlertConfigCache(clock=Clock())
    cache.get(load)
    cache.invalidate()
    cache.get(load)
    assert load.calls == 2


def test_a_different_settings_store_is_read_fresh() -> None:
    load = Loader()
    cache = AlertConfigCache(clock=Clock())
    store_a, store_b = object(), object()
    cache.get(load, source=store_a)
    cache.get(load, source=store_a)
    cache.get(load, source=store_b)
    assert load.calls == 2


def test_a_failed_read_is_not_cached() -> None:
    cache = AlertConfigCache(clock=Clock())

    def broken() -> AlertConfig:
        raise ConnectionError("database down")

    with pytest.raises(ConnectionError):
        cache.get(broken)
    assert cache.get(Loader()) is not None


def test_built_value_is_rebuilt_only_when_the_config_changes() -> None:
    builds = []
    built = BuiltFor(lambda config: builds.append(config) or object())
    first = built.get(CONFIG)
    assert built.get(replace(CONFIG)) is first  # equal config, same notifier
    built.get(replace(CONFIG, alert_recipients=["new@acme.test"]))
    assert len(builds) == 2
