# CERTWATCH — SSL Certificate Expiry Dashboard

Monitor TLS certificate expiry across a fleet of domains. Add domains, a checker
probes each cert's `notAfter` over TLS around the clock, and you get alerts as
certs approach configurable thresholds (default **30 / 14 / 7 / 1** days).

**Single self-contained server.** React SPA + FastAPI + SQLite on one box, with
AWS SES/SNS used only for alert delivery. No Lambda, no DynamoDB, no Docker.

```
                     ┌──────────────── one server (EC2 t2.micro) ─────────────────┐
 Browser ──HTTP──►   │  Nginx ──► React SPA (static build)                         │
                     │    └── /auth /domains /checks /admin ──► FastAPI (uvicorn)   │
                     │                                              │               │
                     │            pure core: probe → status → threshold             │
                     │                                              ▼               │
                     │                                       SQLite (one file)      │
                     │  checker-loop (24×7) ── run_checker ── TLS probe ──► SES/SNS  │
                     └─────────────────────────────────────────────────────────────┘
```

---

## Features

- **Live cert monitoring** — status (ok / warning / expired / unreachable), days
  remaining, expiry date, per-domain last-checked/last-error.
- **24×7 checker** — probes all domains continuously (see the runner below).
- **Add / bulk-import / delete** domains, search + status filters, CSV/JSON export.
- **Alerts** — per-threshold, de-duplicated, to the global recipient via SES/SNS.
- **Downloadable PDF status report** (admin) + per-domain **test alert**.
- **Auth** — username/password JWT with an in-app **Users** admin panel (roles).
- **Dark / light / auto** theme, health-ring overview, 7-day forecast.

---

## Prerequisites

- **Python 3.12+**
- **Node 18+** (for the frontend)

---

## Quick start (local dev)

### 1. Backend
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1           # Windows PowerShell
# source .venv/bin/activate            # macOS/Linux
pip install -e ".[dev]"
```

The repo ships a local `.env` (in-memory store, console notifier, auth on with a
dev secret). Run the API:
```powershell
python -m uvicorn backend.ssl_monitor.api.app:app --reload --port 8000
```
- API: http://localhost:8000 · interactive docs: http://localhost:8000/docs

### 2. Frontend (new terminal)
```powershell
cd frontend
npm install
npm run dev
```
- Dashboard: http://localhost:5173 (proxies API calls to `:8000`, so no CORS)

Log in with **admin / admin123** (seeded on first run from
`AUTH_BOOTSTRAP_PASSWORD`), then add a domain (e.g. `google.com`).

### 3. Populate status — run the checker
There's no manual "check" button; a domain shows `unreachable` until the checker
probes it. Locally, run it from the CLI:
```powershell
python -m backend.runner.run_checker --once           # one pass over stored domains
python -m backend.runner.run_checker --interval 300    # re-check every 5 min (24×7 mode)

# ad-hoc probe (no storage, no auth) — just log the result of a host:
python -m backend.runner.run_checker --check google.com --check expired.badssl.com
```
On a deployed server this runs continuously as the `certwatch-checker-loop`
systemd service (see DEPLOY.md).

---

## Auth & users

- Login issues a JWT (HS256, signed with `AUTH_SECRET`) held in `localStorage`.
- The first run seeds an **admin** account; passwords are PBKDF2-hashed in
  `auth_users.json` (git-ignored).
- Admins manage accounts from the **Users** panel (or `/admin/users`); roles are
  `admin` (full) and `user` (view/monitor, no user/digest management).
- For pure local hacking you can set `AUTH_ENABLED=false` — every request is then
  treated as an anonymous admin (no login screen).

---

## Configuration (`.env`)

| Var | Default | Meaning |
|-----|---------|---------|
| `STORAGE` | `sqlite` | `sqlite` (default) or `memory` (non-persistent dev) |
| `SQLITE_PATH` | `certwatch.db` | SQLite DB file path |
| `AUTH_ENABLED` | `true` | `false` = no login gate (every request is admin) |
| `AUTH_SECRET` | _(insecure default)_ | JWT signing secret — **must** be a real value when auth is on |
| `AUTH_TOKEN_EXPIRE_MINUTES` | `720` | token lifetime |
| `AUTH_USERS_FILE` | `auth_users.json` | user store path |
| `AUTH_BOOTSTRAP_PASSWORD` | `admin123` | seeds the `admin` account on first run |
| `NOTIFIER` | `console` | `console` (log) or `sns_ses` (AWS alerts) |
| `ALERT_EMAIL` | — | SES sender/recipient (for `sns_ses`) |
| `SNS_TOPIC_ARN` | — | SNS topic ARN (for `sns_ses`) |
| `AWS_REGION` | `us-east-1` | region for SES/SNS |
| `THRESHOLD_DAYS` | `30,14,7,1` | alert thresholds (days) |
| `TLS_TIMEOUT_SECONDS` | `5` | per-host connect timeout |

> The API **refuses to start** with `AUTH_ENABLED=true` while `AUTH_SECRET` is the
> built-in default — so a misconfigured deploy fails loudly.

---

## Tests

```powershell
pytest backend/tests -q          # full suite — no Docker, no network, all runs
cd frontend; npm run build       # frontend type-check + production build
```

**What's covered**
- **Pure logic:** threshold/alert decision (idempotency, escalation, renewal
  reset), status classification boundaries, cert parsing for
  valid/expiring/expired/self-signed, and connection failures that must report
  **unreachable — never a false "expiring"**.
- **Adapters:** SQLite round-trip, in-memory store, console notifier, config parsing.
- **API (TestClient):** auth, user management, domain CRUD + validation (409/422),
  the full **add → check → alert → idempotency** flow against real SQLite.

---

## Deployment

Single EC2 t2.micro, fully scripted. See **[DEPLOY.md](DEPLOY.md)** — `deploy/setup.sh`
provisions swap, a service user, the venv, the frontend build, systemd services
(API + 24×7 checker), and Nginx. AWS is only needed for SES/SNS alerts.

---

## Repo structure

```
backend/
  ssl_monitor/
    core/        # PURE: cert_probe, status, thresholds, models (no I/O)
    adapters/
      storage/   # StoragePort + sqlite + memory
      notifier/  # NotifierPort + console + sns_ses  (alerts)
    services/    # checker + domains + digest renderer (adapter-agnostic)
    handlers/    # _deps wiring (config → adapters) + checker entry
    auth/        # JWT security + JSON user store + models
    api/         # FastAPI app, schemas, dependencies
    config/      # Settings (env → typed config)
  runner/        # run_checker CLI — the 24×7 checker loop
  scripts/       # bootstrap (SES topic + SES sender verification)
  tests/         # unit/ + integration/ (SQLite, no external services)
frontend/        # React + Vite + TypeScript dashboard
deploy/          # systemd units, nginx.conf, setup.sh, update.sh, env example
DEPLOY.md        # single-server deployment guide
```

### API surface
| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/auth/login` | username/password → JWT |
| `GET` | `/auth/me` | current user (identity + role) |
| `GET/POST/DELETE` | `/admin/users[/{username}]` | manage users (admin only) |
| `GET` | `/domains` | list monitored domains |
| `POST` | `/domains` | add `{ "domain": "example.com", "port": 443 }` |
| `PATCH` | `/domains/{domain}` | update notify emails / alerts toggle |
| `DELETE` | `/domains/{domain}` | stop monitoring (idempotent) |
| `POST` | `/domains/bulk` | add many at once |
| `POST` | `/domains/{domain}/test-alert` | send a test alert |
| `POST` | `/checks/run` | run the checker over all stored domains |
| `GET` | `/checks/digest` | download a PDF status report (admin only) |
| `GET` | `/health` | liveness |

---

## Design notes

- **Pure core, injected I/O.** The TLS probe takes an injectable connector and
  the threshold logic takes plain values (no clock inside) — so every rule is
  unit-tested deterministically with no network.
- **Unreachable ≠ expiring.** A down/refused/timed-out host is recorded as
  `unreachable` with the error preserved; it never produces a false expiry alert
  and never touches alert-dedup state.
- **Idempotent alerts.** Each run fires at most one alert — the most urgent
  crossed threshold — and only when more urgent than the last one sent
  (`last_alert_threshold`), so the 24×7 loop doesn't re-page you. A renewal resets
  the dedup state.
- **Ports & adapters.** Storage and notifier are `Protocol`s;
  `handlers/_deps.py` is the single seam that turns `Settings` into concrete
  implementations (SQLite, SES/SNS).
