"""FastAPI application.

Thin routes that delegate to the domain/checker services. Each route maps 1:1 to
an API Gateway route so the same surface deploys to Lambda later:

    GET    /domains            -> list_domains
    POST   /domains            -> add_domain
    DELETE /domains/{domain}   -> delete_domain
    POST   /checks/run         -> run_check

CORS is enabled for the Vite dev origin.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware

from ..adapters.mailer.base import MailerPort
from ..adapters.notifier.base import NotifierPort
from ..adapters.storage.base import StoragePort
from ..auth.models import User
from ..auth.security import create_access_token
from ..auth.users import UserStore
from ..config.settings import Settings
from ..core.models import Alert, Status
from ..services.checker import ProbeFn, refresh_status, run_check
from ..services.domains import (
    DomainExists,
    DomainNotFound,
    InvalidDomain,
    InvalidEmail,
    add_domain,
    delete_domain,
    is_valid_email,
    list_domains,
    update_domain,
)
from ..auth.rate_limit import LoginLimiter
from .dependencies import (
    get_app_settings,
    get_cert_prober,
    get_current_user,
    get_login_limiter,
    get_mailer,
    get_notifier,
    get_probe,
    get_storage,
    get_user_store,
    require_admin,
)
from .schemas import (
    AdminUserIn,
    AdminUserOut,
    BulkDomainIn,
    BulkDomainResult,
    CertOut,
    ChangePasswordIn,
    CheckSummary,
    DigestIn,
    DomainIn,
    DomainOut,
    DomainUpdateIn,
    LoginIn,
    MessageOut,
    TokenOut,
    UserOut,
)


ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
# In production set CORS_ORIGINS to the CloudFront URL, e.g.:
# CORS_ORIGINS=https://d1234abcd.cloudfront.net
for _origin in os.environ.get("CORS_ORIGINS", "").split(","):
    _origin = _origin.strip()
    if _origin:
        ALLOWED_ORIGINS.append(_origin)

# The API runs under uvicorn, which never configures our app loggers — so mailer
# and auth log lines (including "OTP for …" and SMTP failures) were invisible.
# Attach one stdout handler to the ssl_monitor tree at INFO so operators can see
# exactly what the mailer/checker are doing.
_app_root = logging.getLogger("ssl_monitor")
if not _app_root.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _app_root.addHandler(_handler)
    _app_root.setLevel(logging.INFO)

logger = logging.getLogger("ssl_monitor.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Fail fast on insecure production config."""
    settings = get_app_settings()
    if settings.auth_enabled and settings.auth_secret_is_insecure:
        raise RuntimeError(
            "AUTH_SECRET is the built-in insecure default. Set a real secret "
            "(e.g. `openssl rand -hex 32`) before running with AUTH_ENABLED=true."
        )
    yield


app = FastAPI(title="SSL Expiry Dashboard API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenOut)
def login(
    body: LoginIn,
    store: UserStore = Depends(get_user_store),
    settings: Settings = Depends(get_app_settings),
    limiter: LoginLimiter = Depends(get_login_limiter),
) -> TokenOut:
    wait = limiter.retry_after(body.username)
    if wait > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {wait}s.",
            headers={"Retry-After": str(wait)},
        )
    user = store.verify(body.username, body.password)
    if user is None:
        limiter.record_failure(body.username)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
        )
    limiter.reset(body.username)
    token = create_access_token(
        user.username, user.role, settings.auth_secret, settings.token_expire_minutes
    )
    return TokenOut(access_token=token, user=UserOut(username=user.username, role=user.role))


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    """Return the authenticated user (identity + role)."""
    return UserOut(username=user.username, role=user.role)


@app.post("/auth/change-password", response_model=MessageOut)
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    store: UserStore = Depends(get_user_store),
) -> MessageOut:
    if len(body.new_password) < 8:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must be at least 8 characters.",
        )
    if store.verify(user.username, body.current_password) is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect."
        )
    store.set_password(user.username, body.new_password)
    return MessageOut(message="Password changed.")


# ── Admin: user management (admin role required) ────────────────


@app.get("/admin/users", response_model=list[AdminUserOut])
def admin_list_users(
    store: UserStore = Depends(get_user_store),
    _admin: User = Depends(require_admin),
) -> list[AdminUserOut]:
    return [AdminUserOut(username=u.username, role=u.role) for u in store.list()]


@app.post("/admin/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def admin_add_user(
    body: AdminUserIn,
    store: UserStore = Depends(get_user_store),
    _admin: User = Depends(require_admin),
) -> AdminUserOut:
    username = body.username.strip()
    if not username:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username required")
    if not body.password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="password required")
    if store.get(username) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="username already exists")
    user = store.add(username, body.password, role=body.role)
    return AdminUserOut(username=user.username, role=user.role)


@app.delete("/admin/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_user(
    username: str,
    store: UserStore = Depends(get_user_store),
    admin: User = Depends(require_admin),
) -> Response:
    if username == admin.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot delete your own account")
    store.delete(username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/domains", response_model=list[DomainOut])
def get_domains(
    storage: StoragePort = Depends(get_storage),
    _user: User = Depends(get_current_user),
) -> list[DomainOut]:
    return [DomainOut.from_record(r) for r in list_domains(storage)]


@app.post("/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
def post_domain(
    body: DomainIn,
    storage: StoragePort = Depends(get_storage),
    settings: Settings = Depends(get_app_settings),
    probe: ProbeFn = Depends(get_probe),
    _user: User = Depends(get_current_user),
) -> DomainOut:
    now = datetime.now(timezone.utc)
    try:
        record = add_domain(storage, body.domain, body.port, now)
    except DomainExists as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidDomain as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    # Probe immediately so the new row shows real status without waiting for the
    # periodic checker. Doesn't touch alert-dedup, so the first alert still fires.
    refresh_status(record, now=now, thresholds=settings.threshold_days,
                   timeout=settings.tls_timeout_seconds, probe_fn=probe)
    storage.put(record)
    return DomainOut.from_record(record)


@app.patch("/domains/{domain}", response_model=DomainOut)
def patch_domain(
    domain: str,
    body: DomainUpdateIn,
    storage: StoragePort = Depends(get_storage),
    _user: User = Depends(get_current_user),
) -> DomainOut:
    try:
        record = update_domain(
            storage,
            domain,
            notify_emails=body.notify_emails,
            alerts_enabled=body.alerts_enabled,
        )
    except DomainNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidEmail as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return DomainOut.from_record(record)


@app.delete("/domains/{domain}", status_code=status.HTTP_204_NO_CONTENT)
def remove_domain(
    domain: str,
    storage: StoragePort = Depends(get_storage),
    _user: User = Depends(get_current_user),
) -> Response:
    delete_domain(storage, domain)  # idempotent → 204 whether or not it existed
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/domains/bulk", response_model=BulkDomainResult)
def bulk_add_domains(
    body: BulkDomainIn,
    storage: StoragePort = Depends(get_storage),
    settings: Settings = Depends(get_app_settings),
    probe: ProbeFn = Depends(get_probe),
    _user: User = Depends(get_current_user),
) -> BulkDomainResult:
    now = datetime.now(timezone.utc)
    added, failed = [], []
    for item in body.domains:
        try:
            record = add_domain(storage, item.domain, item.port, now)
            refresh_status(record, now=now, thresholds=settings.threshold_days,
                           timeout=settings.tls_timeout_seconds, probe_fn=probe)
            storage.put(record)
            added.append(DomainOut.from_record(record))
        except (DomainExists, InvalidDomain) as exc:
            failed.append({"domain": item.domain, "error": str(exc)})
    return BulkDomainResult(added=added, failed=failed)


@app.post("/domains/{domain}/test-alert", response_model=MessageOut)
def test_alert(
    domain: str,
    storage: StoragePort = Depends(get_storage),
    notifier: NotifierPort = Depends(get_notifier),
    _user: User = Depends(get_current_user),
) -> MessageOut:
    records = list_domains(storage)
    record = next((r for r in records if r.domain == domain), None)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="domain not found")
    alert = Alert(
        domain=record.domain,
        status=record.status if record.status != Status.OK else Status.WARNING,
        days_remaining=record.days_remaining if record.days_remaining is not None else 14,
        threshold=14,
        not_after=record.not_after,
        recipients=record.notify_emails,
    )
    notifier.notify(alert)
    return MessageOut(
        message=(
            f"Test alert dispatched for {record.host}. Check the recipient inbox "
            "(SNS/SES) or the server logs to confirm delivery."
        )
    )


@app.get("/domains/{domain}/cert", response_model=CertOut)
def get_cert(
    domain: str,
    storage: StoragePort = Depends(get_storage),
    settings: Settings = Depends(get_app_settings),
    prober=Depends(get_cert_prober),
    _user: User = Depends(get_current_user),
) -> CertOut:
    """Fetch full certificate details live (issuer, SANs, key, etc.)."""
    record = next((r for r in list_domains(storage) if r.domain == domain), None)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="domain not found")
    try:
        info = prober(record.host, record.port, settings.tls_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - any probe failure → clean 502
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Could not read certificate: {exc}"
        ) from exc
    return CertOut(
        issuer=info.issuer,
        subject=info.subject,
        serial=info.serial,
        sig_algorithm=info.sig_algorithm,
        key_type=info.key_type,
        key_bits=info.key_bits,
        not_before=info.not_before,
        not_after=info.not_after,
        sans=info.sans,
    )


@app.post("/checks/digest", response_model=MessageOut)
def send_digest(
    body: DigestIn,
    storage: StoragePort = Depends(get_storage),
    mailer: MailerPort = Depends(get_mailer),
    _admin: User = Depends(require_admin),
) -> MessageOut:
    # Admin-only: the digest discloses the full monitored-domain inventory.
    if not is_valid_email(body.email):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid recipient email"
        )
    domains = list_domains(storage)
    mailer.send_digest(body.email, domains)
    return MessageOut(message=f"Digest sent to {body.email}")


@app.post("/checks/run", response_model=CheckSummary)
def run_checks(
    storage: StoragePort = Depends(get_storage),
    notifier: NotifierPort = Depends(get_notifier),
    settings: Settings = Depends(get_app_settings),
    probe: ProbeFn = Depends(get_probe),
    _user: User = Depends(get_current_user),
) -> CheckSummary:
    summary = run_check(
        storage,
        notifier,
        thresholds=settings.threshold_days,
        timeout=settings.tls_timeout_seconds,
        now=datetime.now(timezone.utc),
        probe_fn=probe,
    )
    return CheckSummary(**summary)
