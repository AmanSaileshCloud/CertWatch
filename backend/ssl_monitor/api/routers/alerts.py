"""Uptime alerts: test email, the global alert settings, and alert rules (extra recipients)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ...adapters.notifier.base import NotifierPort
from ...auth.models import User
from ...config.settings import Settings
from ...core.app_settings_models import AlertConfig
from ...services import alert_rules
from ...services.catalog import Stores
from ...services.alert_report import resolve_host_ip
from ...services.uptime_alerts import AlertNotSent, default_recipients, send_test_alert
from ..cooldown import Cooldown
from ..dependencies import (
    get_alert_config,
    get_alert_notifier,
    get_app_settings,
    get_current_user,
)
from ..schemas import MessageOut
from ..uptime_schemas import (
    AlertRuleIn,
    AlertRuleOut,
    AlertRulePatch,
    AlertSettingsOut,
    provided_fields,
)
from . import get_stores, utc_now

router = APIRouter(tags=["alerts"], dependencies=[Depends(get_current_user)])

TEST_ALERT_COOLDOWN_SECONDS = 30
_test_alert_cooldown = Cooldown(TEST_ALERT_COOLDOWN_SECONDS)


def get_test_alert_cooldown() -> Cooldown:
    """Per-monitor wait between test alerts (one per process; tests override it)."""
    return _test_alert_cooldown


@router.post("/monitors/{monitor_id}/test-alert", response_model=MessageOut)
def test_alert(
    monitor_id: int,
    stores: Stores = Depends(get_stores),
    notifier: NotifierPort = Depends(get_alert_notifier),
    config: AlertConfig = Depends(get_alert_config),
    cooldown: Cooldown = Depends(get_test_alert_cooldown),
) -> MessageOut:
    """Starts the cooldown whenever the email provider was contacted (sent or refused)."""
    wait = cooldown.remaining(monitor_id)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {wait} s before sending another test alert",
            headers={"Retry-After": str(wait)},
        )
    try:
        to = send_test_alert(
            stores.monitors, stores.catalog, notifier, monitor_id,
            now=utc_now(), settings_like=config, resolve_ip=resolve_host_ip,
        )
    except AlertNotSent as exc:
        cooldown.start(monitor_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    cooldown.start(monitor_id)
    return MessageOut(message=f"Test alert sent to {', '.join(to)}")


@router.get("/alerts/settings", response_model=AlertSettingsOut)
def alert_settings(
    config: AlertConfig = Depends(get_alert_config),
    settings: Settings = Depends(get_app_settings),
) -> AlertSettingsOut:
    """Effective values: Settings page, else environment, else defaults."""
    return AlertSettingsOut(
        default_recipients=default_recipients(config),
        reminder_minutes=config.uptime_reminder_minutes,
        alert_delay_minutes=config.uptime_alert_delay_minutes,
        notifier=settings.notifier,
        sender=config.alert_email,
    )


@router.get("/alert-rules", response_model=list[AlertRuleOut])
def list_alert_rules(
    customer_id: int | None = None,
    monitor_id: int | None = None,
    stores: Stores = Depends(get_stores),
) -> list[AlertRuleOut]:
    rules = alert_rules.list_rules(stores.catalog, customer_id=customer_id, monitor_id=monitor_id)
    return [AlertRuleOut.from_rule(r) for r in rules]


def _require_admin_for_global(customer_id: int | None, monitor_id: int | None, user: User) -> None:
    """Global rules receive every customer's alerts, like the admin-only default
    recipients on the Settings page, so only admins may manage them."""
    if alert_rules.is_global(customer_id, monitor_id) and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Only admins can manage global alert rules")


def _require_admin_for_global_rule(stores: Stores, rule_id: int, user: User) -> None:
    rule = alert_rules.get_rule(stores.catalog, rule_id)
    _require_admin_for_global(rule.customer_id, rule.monitor_id, user)


@router.post("/alert-rules", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
def create_alert_rule(
    body: AlertRuleIn,
    stores: Stores = Depends(get_stores),
    user: User = Depends(get_current_user),
) -> AlertRuleOut:
    _require_admin_for_global(body.customer_id, body.monitor_id, user)
    rule = alert_rules.create_rule(stores.catalog, stores.monitors, body.model_dump())
    return AlertRuleOut.from_rule(rule)


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleOut)
def update_alert_rule(
    rule_id: int,
    body: AlertRulePatch,
    stores: Stores = Depends(get_stores),
    user: User = Depends(get_current_user),
) -> AlertRuleOut:
    _require_admin_for_global_rule(stores, rule_id, user)
    rule = alert_rules.update_rule(stores.catalog, rule_id, provided_fields(body))
    return AlertRuleOut.from_rule(rule)


@router.delete("/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert_rule(
    rule_id: int,
    stores: Stores = Depends(get_stores),
    user: User = Depends(get_current_user),
) -> Response:
    _require_admin_for_global_rule(stores, rule_id, user)
    alert_rules.delete_rule(stores.catalog, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
