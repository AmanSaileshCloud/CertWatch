"""Admin Settings page: alert recipients/timing and SES email delivery (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ...adapters.notifier.base import NotifierPort
from ...adapters.secret_box import SecretBox
from ...adapters.storage.base import SettingsStorePort
from ...auth.models import User
from ...config.settings import Settings
from ...core.app_settings_models import AlertConfig
from ...handlers._deps import delivery_method
from ...services.alert_config_cache import AlertConfigCache
from ...services.app_settings import (
    EmailNotDelivered,
    describe_alert_settings,
    send_test_email,
    update_alert_settings,
)
from ..dependencies import (
    get_alert_config,
    get_alert_config_cache,
    get_alert_notifier,
    get_app_settings,
    get_secret_box,
    get_settings_store,
    require_admin,
)
from ..schemas import MessageOut
from ..settings_schemas import AlertSettingsAdminOut, AlertSettingsIn, SendTestEmailIn
from . import utc_now

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_admin)])


@router.get("/alerts", response_model=AlertSettingsAdminOut)
def get_alert_settings(
    settings: Settings = Depends(get_app_settings),
    store: SettingsStorePort = Depends(get_settings_store),
    box: SecretBox | None = Depends(get_secret_box),
) -> AlertSettingsAdminOut:
    return AlertSettingsAdminOut(**describe_alert_settings(store, settings, box))


@router.put("/alerts", response_model=AlertSettingsAdminOut)
def put_alert_settings(
    body: AlertSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    store: SettingsStorePort = Depends(get_settings_store),
    box: SecretBox | None = Depends(get_secret_box),
    cache: AlertConfigCache = Depends(get_alert_config_cache),
) -> AlertSettingsAdminOut:
    changes = body.model_dump(mode="json", exclude_unset=True)
    view = update_alert_settings(store, settings, box, changes, user=admin.username, now=utc_now())
    cache.invalidate()  # this process shows the new values at once (others within 30 s)
    return AlertSettingsAdminOut(**view)


@router.post("/alerts/test-email", response_model=MessageOut)
def post_test_email(
    body: SendTestEmailIn,
    config: AlertConfig = Depends(get_alert_config),
    notifier: NotifierPort = Depends(get_alert_notifier),
    settings: Settings = Depends(get_app_settings),
) -> MessageOut:
    method = delivery_method(config, settings)
    try:
        to = send_test_email(notifier, config, body.to)
    except EmailNotDelivered as exc:
        # SesSmtpNotifier keeps the provider's reply (no secrets) in last_error.
        reason = getattr(notifier, "last_error", None) or "see the server log for details"
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail=f"{method} did not accept the test email: {reason}") from exc
    return MessageOut(message=f"Test email sent to {to} via {method}")
