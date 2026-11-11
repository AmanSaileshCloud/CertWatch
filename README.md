# CERTWATCH: SSL certificate and website uptime monitoring

CertWatch watches two things for every customer:

1. **SSL certificates.** It checks when each certificate expires and alerts at 30 / 14 / 7 / 1 days. Private hosts can be checked through AWS SSM.
2. **Website uptime.** It checks each website every 1, 5 or 10 minutes. After 3 failed checks in a row it records an outage (an *incident*). It emails when a site goes DOWN, sends optional reminders, and emails again when the site RECOVERS.

Websites are organised **Customer → Group → websites**. Reports show uptime % per website and per customer, list every outage, and export to CSV.

It runs on **one server**: a React frontend, a FastAPI backend, two background checkers and PostgreSQL. Alerts go out through **Amazon SES** (and SNS for SSL alerts).

```
                ┌───────────────────────── one EC2 server ──────────────────────────┐
 Browser ─────► │ Nginx ──► React SPA (static)                                      │
   HTTPS        │   └─ API routes ──► FastAPI (uvicorn) ──┐                         │
                │                                         ▼                         │
                │ certwatch-checker-loop (SSL, 24x7) ──► PostgreSQL ◄── rollup/backup│
                │ certwatch-uptime (websites, 24x7)  ──┘      timers                 │
                └──────────────────────────────┬────────────────────────────────────┘
                                               ▼
                                  Amazon SES / SNS (alert emails)
```

---

## Features

**Websites (uptime)**
- Add, edit, pause and delete monitors. Each has a URL, a check interval, a timeout, an expected HTTP status and an optional keyword that must appear on the page.
- Checks run in parallel with async `httpx`. Requests to private or internal addresses are blocked (SSRF guard) unless allowed.
- A site is DOWN only after 3 failures in a row, which filters out short blips. Each incident records its start, end, duration and reason.
- Alert emails go out on DOWN and RECOVERED, with optional reminders every N minutes and an optional delay. There's one alert per incident, delivered at least once.
- Extra recipients can be added per customer or per website (alert rules).
- The dashboard shows UP/DOWN/PAUSED, last response time and 24-hour uptime. Each website has a detail page with a response-time chart, recent checks and incident history.
- **Reports** (Reports page or `/reports`):
  - uptime % per website and per customer, by day, week, month or the whole range, in IST;
  - a downtime log;
  - CSV export.

**SSL certificates**
- Status (ok / warning / expired / unreachable), days remaining and expiry date.
- Bulk import, search and filters, CSV/JSON export, a PDF status report and a test alert.
- Threshold alerts are de-duplicated. An unreachable host never triggers a false "expiring" alert.

**Admin**
- Username/password login (JWT) with `admin` and `user` roles, managed in the Users panel.
- A **Settings** page: default alert recipients, reminder and delay timing, and SES delivery (IAM role, or SES SMTP with the password stored encrypted).

**Operations**
- An hourly job summarises check results and deletes raw results older than 30 days.
- A nightly database backup, optionally copied to S3.
- `/health/ready` shows whether the database and the uptime worker are healthy. An AWS alarm watches it (see DEPLOY.md).

---

## Local development

**You need:** Python 3.12+, Node 20+, and Docker (for PostgreSQL).

```powershell
# 1. Settings: copy the template and fill in POSTGRES_PASSWORD, AUTH_SECRET,
#    AUTH_BOOTSTRAP_PASSWORD and SETTINGS_ENCRYPTION_KEY (the file explains each).
copy .env.example .env

# 2. Database (PostgreSQL 17 in Docker; also creates the certwatch_test database)
docker compose up -d

# 3. Backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head                    # create / update the tables
python -m uvicorn backend.ssl_monitor.api.app:app --reload --port 8000

# 4. Background workers (each in its own terminal; both optional for UI work)
python -m backend.runner.run_uptime                     # website checks + alert emails
python -m backend.runner.run_checker --interval 300     # SSL certificate checks

# 5. Frontend
cd frontend
npm install
npm run dev
```

- Dashboard: http://localhost:5173. It forwards API calls to `:8000`; the forwarded paths are listed in `frontend/vite.config.ts`.
- API docs: http://localhost:8000/docs
- Log in as **admin** with your `AUTH_BOOTSTRAP_PASSWORD`.

With `NOTIFIER=console` (the default), alert emails are printed in the worker's log instead of being sent.

**Handy one-off commands:**
```powershell
python -m backend.runner.run_uptime --check https://example.com   # probe a URL, save nothing
python -m backend.runner.run_uptime --once                        # one check cycle
python -m backend.runner.run_checker --check google.com           # probe a certificate
python -m backend.runner.run_rollup                               # hourly summary + cleanup now
python -m backend.scripts.migrate_sqlite_to_postgres --sqlite certwatch.db --dry-run
```

**Changing the database:** add a migration in `backend/migrations/versions/` (next number, `down_revision` = previous), update `backend/ssl_monitor/adapters/db/tables.py` to match, then run `alembic upgrade head`.

---

## Tests

```powershell
pytest -q                          # backend: unit + PostgreSQL integration tests
cd frontend; npx tsc --noEmit      # frontend type check
npm run build                      # production build
```

The PostgreSQL tests run against `TEST_DATABASE_URL` (the `certwatch_test` database that docker compose creates). They're **skipped** if it isn't set, so the unit tests still run without Docker.

**What's covered:**
- the pure rules: certificate thresholds, incident rules, alert timing, uptime %, report periods in IST, rollup windows, health checks;
- every API route, including auth and roles;
- both stores (in-memory and PostgreSQL), including concurrency;
- the migrations;
- the deploy files: every API route must be forwarded by `deploy/nginx.conf` and `vite.config.ts`, and every systemd unit must be installed by the scripts.

---

## Configuration

Everything is set with environment variables, read from `.env` locally or `/etc/certwatch/certwatch.env` on the server. No secrets are kept in code. The main ones:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL connection, e.g. `postgresql://certwatch:…@localhost:5432/certwatch` |
| `STORAGE` | `postgres` | `postgres`, or `memory` (temporary, for experiments) |
| `AUTH_SECRET` | — | JWT signing key, 32+ random characters (`openssl rand -hex 32`) |
| `AUTH_BOOTSTRAP_PASSWORD` | — | password of the `admin` account created on first run |
| `AUTH_USERS_FILE` | `auth_users.json` | where user accounts are stored |
| `SETTINGS_ENCRYPTION_KEY` | — | Fernet key that encrypts the SMTP password saved on the Settings page |
| `NOTIFIER` | `console` | `console` (log only) or `sns_ses` (real emails) |
| `ALERT_EMAIL` / `ALERT_RECIPIENTS` | — | SES sender and default recipients (the Settings page can override them) |
| `AWS_REGION` | `us-east-1` | region for SES/SNS (`ap-south-1` in production) |
| `SNS_TOPIC_ARN` | — | SNS topic for SSL alerts |
| `THRESHOLD_DAYS` | `30,14,7,1` | SSL alert thresholds |
| `UPTIME_TICK_SECONDS` | `10` | how often the uptime worker looks for due checks |
| `UPTIME_MAX_CONCURRENCY` | `50` | sites checked at the same time |
| `UPTIME_FAILURE_THRESHOLD` / `UPTIME_RECOVERY_THRESHOLD` | `3` / `1` | failures before DOWN, successes before UP |
| `UPTIME_REMINDER_MINUTES` / `UPTIME_ALERT_DELAY_MINUTES` | `30` / `0` | default alert timing |
| `UPTIME_ALLOW_PRIVATE_TARGETS` | `false` | allow monitoring internal addresses |
| `UPTIME_RAW_RETENTION_DAYS` | `30` | raw check results kept; hourly summaries are kept forever |
| `REPORT_TIMEZONE` | `Asia/Kolkata` | time zone of report days, weeks and months |
| `CERTWATCH_PUBLIC_URL` | — | dashboard address used in email links |
| `BACKUP_S3_BUCKET` / `BACKUP_KEEP_LOCAL` | — / `7` | nightly backup copy to S3, and local copies kept |

`.env.example` (local) and `deploy/certwatch.env.example` (server) list every setting with an explanation.

> **Startup guard.** With `AUTH_ENABLED=true`, the API **refuses to start** if `AUTH_SECRET` is empty, too short, or still placeholder text such as `CHANGE_ME`. The same applies if `AUTH_BOOTSTRAP_PASSWORD` is a placeholder. On a server, `deploy/setup.sh` generates real values.

---

## Deployment

See **[DEPLOY.md](DEPLOY.md)**. In short, on a fresh Ubuntu 24.04 EC2 (t3.medium):

```bash
sudo CERTWATCH_DOMAIN=certwatch.example.com CERTBOT_EMAIL=ops@example.com bash deploy/setup.sh
```

This installs PostgreSQL, generates every secret, applies the migrations, starts all services and timers, and turns on HTTPS. Use `sudo bash deploy/update.sh` for later releases: it backs up first, then migrates and restarts.

---

## Repository layout

```
backend/
  ssl_monitor/
    core/       PURE rules, no I/O: cert/uptime probes' judgement, incident rules,
                alert policy, report maths, rollup windows, health
    adapters/   the outside world: db/ (tables, engine), storage/ (PostgreSQL +
                in-memory), notifier/ (console, SES API, SES SMTP), secret box, SSM
    services/   use cases that combine rules and adapters (checker, monitors,
                alerts, reports, CSV, settings, rollup)
    api/        FastAPI app, routers/ (one file per area), schemas, dependencies
    auth/       JWT, password hashing, user store, login rate limit
    config/     Settings: environment variables → typed config
    handlers/   _deps.py: the one place that picks concrete adapters
  runner/       background processes: run_uptime, run_checker, run_rollup
  migrations/   Alembic database migrations (0001…)
  scripts/      bootstrap (SNS/SES), SQLite → PostgreSQL copy
  tests/        unit/ and integration/ (PostgreSQL)
frontend/       React + Vite + TypeScript dashboard
deploy/         setup.sh, update.sh, backup.sh, lib.sh, systemd units and timers,
                nginx.conf, security headers, production env template
DEPLOY.md       production deployment and operations guide
```

### API overview

Interactive docs are at `/docs`. Every route except `/auth/login` and `/health*` needs a login.

| Area | Routes |
|---|---|
| Auth & users | `POST /auth/login`, `GET /auth/me`, `POST /auth/change-password`, `/admin/users` (admin) |
| SSL | `/domains` (CRUD, `/bulk`, `/{domain}/cert`, `/{domain}/test-alert`), `POST /checks/run`, `GET /checks/digest` (PDF) |
| Customers & groups | `/customers`, `/groups`, `/groups/{id}/members` |
| Websites | `/monitors` (CRUD), `/{id}/pause`, `/{id}/resume`, `/{id}/checks`, `/{id}/series`, `/{id}/incidents`, `/{id}/test-alert` |
| Alerts | `GET /alerts/settings`, `/alert-rules` (global rules are admin-only) |
| Settings | `GET/PUT /settings/alerts`, `POST /settings/alerts/test-email` (admin) |
| Reports | `GET /reports/uptime[.csv]`, `GET /reports/downtime[.csv]`. Parameters: `start`, `end`, `granularity`, `customer_id`, `group_id`, `level` |
| Health | `GET /health` (process up), `GET /health/ready` (database + uptime worker) |

---

## Design notes

- **Pure core, injected I/O.** The rules in `core/` take plain values, including "now", so each one is unit-tested without a network, a database or a clock.
- **Ports and adapters.** Storage and notifiers are `Protocol`s. `handlers/_deps.py` turns settings into concrete adapters, so the tests swap in in-memory versions.
- **One transaction per check batch.** Results, monitor state and incident open/close are saved together. A partial unique index allows only one open incident per website.
- **At-least-once alerts.** A DOWN or RECOVERED email is marked sent only after the provider accepts it. Failures back off and retry, and an advisory lock stops two workers sending the same alert.
- **Unreachable ≠ expiring.** A host that can't be reached is shown as `unreachable` and never triggers a false certificate-expiry alert.
- **Time-based uptime.** Report uptime is `1 − downtime / monitored time`, counting confirmed outages from the first failed check. That's how SLAs are measured, so report figures can be compared with Site24x7's.
