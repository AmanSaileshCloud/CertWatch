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

## Deploying to production: step by step

This puts CertWatch on one AWS EC2 server in Mumbai (`ap-south-1`) with HTTPS, real alert emails, nightly backups and an alarm that tells you if CertWatch itself stops. Do the steps in order; each one ends with a **Check** so you know it worked before moving on. [DEPLOY.md](DEPLOY.md) has the same steps in more depth plus day-to-day operations.

Replace these example values with your own everywhere below:

| Example value | What it stands for |
|---|---|
| `certwatch.yourcompany.com` | the web address CertWatch will live at |
| `alerts@yourcompany.com` | the email address alerts are sent **from** |
| `ops@yourcompany.com` | your team's email (alarms and certificate notices) |
| `yourcompany-certwatch-backups` | the S3 bucket name for backups (must be unique worldwide) |
| `123456789012` | your AWS account ID (top-right menu in the AWS console) |

**You need:** admin access to the AWS console, the right to add DNS records for your domain, and about an hour. Start step 3 first if you can: Amazon can take up to a day to approve sending real emails.

---

### Step 1: Put the finished code on GitHub (your laptop)

The server downloads the code from GitHub, so everything must be merged into `main` and pushed. In the project folder:

```powershell
git checkout main
git pull
git merge feature/uptime-monitoring
pytest -q                     # everything must pass
git push origin main
git tag v1.0.0
git push origin v1.0.0        # a "tag" marks exactly which version is in production
```

**Check:** https://github.com/Ritesh5-dev/CertWatch shows your latest commit on `main`, and the tag `v1.0.0` appears under "Tags".

> Your local `.env` file is never uploaded: git ignores it. Production gets its own, freshly generated secrets in step 8.

### Step 2: Create the server's permissions (IAM role)

The server uses an *IAM role* instead of stored AWS keys: AWS gives it short-lived permissions automatically.

1. AWS console → **IAM** → **Roles** → **Create role**.
2. Trusted entity: **AWS service**, use case **EC2** → Next.
3. Attach the managed policy **`AmazonSSMManagedInstanceCore`** (lets you open a terminal on the server from the browser) → Next.
4. Name it `certwatch-server` → **Create role**.
5. Open the role → **Add permissions** → **Create inline policy** → **JSON**, paste (fix the account ID and bucket name), name it `certwatch-app`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ses:SendEmail", "ses:SendRawEmail"], "Resource": "*" },
    { "Effect": "Allow", "Action": "sns:Publish", "Resource": "arn:aws:sns:ap-south-1:123456789012:ssl-alerts" },
    { "Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::yourcompany-certwatch-backups/certwatch/*" },
    { "Effect": "Allow", "Action": ["ssm:SendCommand", "ssm:GetCommandInvocation"], "Resource": "*" }
  ]
}
```

**Check:** the role `certwatch-server` lists two permissions: `AmazonSSMManagedInstanceCore` and `certwatch-app`.

### Step 3: Allow sending email (Amazon SES)

Switch the console region (top right) to **Asia Pacific (Mumbai) ap-south-1** first.

1. **SES** → **Identities** → **Create identity** → **Domain** → `yourcompany.com` → keep "Easy DKIM" → Create.
2. SES shows **3 CNAME records**. Add them at your DNS provider. Also add, if you don't already have them:
   - a TXT record on `yourcompany.com`: `v=spf1 include:amazonses.com ~all` (if an SPF record already exists, add `include:amazonses.com` to it instead of creating a second one)
   - a TXT record on `_dmarc.yourcompany.com`: `v=DMARC1; p=none; rua=mailto:ops@yourcompany.com`
3. **SES** → **Account dashboard** → **Request production access**. Use case: "Transactional: monitoring alerts to our own engineers and our customers' technical contacts, about 1,000 emails a month". Until approved, SES only delivers to addresses you have verified yourself.

**Check:** the identity status turns **Verified** (DNS can take up to an hour), and later the dashboard says your account is **out of the sandbox**.

### Step 4: Create the backup bucket (S3)

1. **S3** → **Create bucket** → name `yourcompany-certwatch-backups`, region **ap-south-1**.
2. Keep **Block all public access** ON and default encryption ON → **Create**.
3. Open the bucket → **Management** → **Create lifecycle rule** → prefix `certwatch/` → **Expire current versions** after **30** days → Create.

**Check:** the bucket exists and its lifecycle rule shows "Expire after 30 days".

### Step 5: Launch the server (EC2)

**EC2** → **Launch instance**:

| Setting | Value |
|---|---|
| Name | `certwatch-prod` |
| Image (AMI) | **Ubuntu Server 24.04 LTS** (64-bit x86) |
| Instance type | **t3.medium** (4 GB memory) |
| Key pair | "Proceed without a key pair" (you will connect through the browser in step 7) |
| Network → Security group | Create new. Rules: **HTTPS (443)** from Anywhere, **HTTP (80)** from Anywhere. **No SSH (22) rule.** |
| Storage | **30 GB gp3** |
| Advanced details → IAM instance profile | **`certwatch-server`** (from step 2) |

Click **Launch instance**.

**Check:** the instance shows **Running** and "2/2 checks passed" after a few minutes.

### Step 6: Give the server a fixed address and a name

1. **EC2** → **Elastic IPs** → **Allocate** → select it → **Actions → Associate** → choose `certwatch-prod`. (Without this, the address changes every time the server is stopped.)
2. At your DNS provider add an **A record**: `certwatch.yourcompany.com` → the Elastic IP.

**Check** (on your laptop, may take a few minutes):
```powershell
nslookup certwatch.yourcompany.com     # must answer with the Elastic IP
```

### Step 7: Open a terminal on the server

**EC2** → select `certwatch-prod` → **Connect** → **Session Manager** → **Connect**. A terminal opens in your browser. (If Session Manager is greyed out, wait 5 minutes: the role needs a moment to register.)

Become the normal Ubuntu user so commands behave as expected:
```bash
sudo su - ubuntu
```

### Step 8: Install CertWatch (one command)

```bash
sudo apt-get update -y && sudo apt-get install -y git
git clone --branch v1.0.0 https://github.com/Ritesh5-dev/CertWatch.git ~/certwatch-src
cd ~/certwatch-src
sudo CERTWATCH_DOMAIN=certwatch.yourcompany.com CERTBOT_EMAIL=ops@yourcompany.com bash deploy/setup.sh
```

This takes about 5–10 minutes. It installs PostgreSQL, Python, Node and Nginx; creates the database; **generates every secret**; builds the dashboard; creates the tables; starts all services and timers; and gets the HTTPS certificate.

**At the end it prints the admin password once. Copy it into your password manager now.**

**Check:** the last lines say `✓ CERTWatch is up.` and do **not** show the "NOT SAFE YET" warning (that warning means HTTPS failed: usually the DNS record from step 6 isn't visible yet: wait and run the same `setup.sh` command again; it is safe to repeat).

### Step 9: Fill in the email and backup settings

```bash
sudo nano /etc/certwatch/certwatch.env
```

Change these lines (use the arrow keys; `Ctrl+O` then `Enter` saves, `Ctrl+X` exits):

```
ALERT_EMAIL=alerts@yourcompany.com        # sender; its domain must be verified in step 3
BACKUP_S3_BUCKET=yourcompany-certwatch-backups
SNS_TOPIC_ARN=                            # leave EMPTY unless you set up the optional SNS topic (DEPLOY.md §1)
```

Leave everything else as it is: `NOTIFIER=sns_ses` and `AWS_REGION=ap-south-1` are already set, and the secrets were generated for you. Never paste this file anywhere; it contains the passwords.

Then restart so the services read the new values:
```bash
sudo systemctl restart certwatch-api certwatch-uptime certwatch-checker-loop
```

### Step 10: Check that everything runs

```bash
curl -s https://certwatch.yourcompany.com/health/ready
systemctl --no-pager status certwatch-api certwatch-uptime certwatch-checker-loop | grep -E "●|Active"
systemctl list-timers 'certwatch-*'
```

**Check:**
- the first command prints `{"status":"ok", ...}` (if it says the uptime worker "has never reported in", wait 30 seconds and retry);
- all three services say **active (running)**;
- two timers are listed: `certwatch-rollup` (hourly) and `certwatch-backup` (nightly).

Now open **https://certwatch.yourcompany.com** in your browser, log in as **admin** with the password from step 8, then **change it right away** (lock icon next to your name, top right). Create accounts for your team in **Users**.

### Step 11: Test the important paths

1. **Email:** **Settings** → send a test email to yourself. It must arrive in the inbox, not spam.
2. **Outage alert:** **Websites** → **Add website** → URL `https://httpbin.org/status/503`, check every **1 minute**, expected status 200. After 3 failed checks (about 3 minutes) it turns **DOWN** and you get a DOWN email. Then delete it. (Editing the URL or expected status instead would close the outage as "settings edited", which by design sends no RECOVERED email; RECOVERED emails go out when a real site comes back.)
3. **Backup:**
   ```bash
   sudo systemctl start certwatch-backup && journalctl -u certwatch-backup -n 5 --no-pager
   ```
   The last line says `Backup complete`, and the file appears in the S3 bucket under `certwatch/`.

### Step 12: Get told if CertWatch itself stops

If the server or the checker dies, nobody gets outage emails, so add an outside alarm that watches `https://certwatch.yourcompany.com/health/ready`. Follow **[DEPLOY.md §5](DEPLOY.md#5-monitoring-the-monitor-do-not-skip)** (copy-paste AWS commands, about 10 minutes; costs roughly US$1 a month).

**Check:** run `sudo systemctl stop certwatch-uptime` on the server. Within about 5 minutes you get an alarm email. Run `sudo systemctl start certwatch-uptime` and an "OK" email follows.

**CertWatch is live.** Add your customers, groups and websites, and run it side by side with Site24x7 for two weeks before switching Site24x7 off.

---

### Releasing a new version later

On your laptop, merge the changes into `main`, run the tests, then tag and push a new version:
```powershell
git tag v1.0.1
git push origin main v1.0.1
```
On the server (Session Manager, then `sudo su - ubuntu`):
```bash
cd ~/certwatch-src
git fetch --tags && git checkout v1.0.1
sudo bash deploy/update.sh
curl -s https://certwatch.yourcompany.com/health/ready
```
`update.sh` backs up the database first, then installs, migrates and restarts. If anything fails it stops, and the old version keeps running. To roll back: `git checkout v1.0.0`, run `update.sh` again, and if the database changed, restore the backup it made (DEPLOY.md §6).

### If something goes wrong

| Problem | What to do |
|---|---|
| `setup.sh` ends with "NOT SAFE YET" | DNS isn't pointing at the server yet. Check step 6 with `nslookup`, then run the same `setup.sh` command again. |
| Browser shows "502 Bad Gateway" | The API isn't running: `journalctl -u certwatch-api -n 30 --no-pager` shows why. |
| `/health/ready` says "database is unreachable" | `sudo systemctl status postgresql`, then `sudo systemctl restart postgresql`. |
| `/health/ready` says the uptime worker stopped | `journalctl -u certwatch-uptime -n 50 --no-pager`, then `sudo systemctl restart certwatch-uptime`. |
| No alert emails | Settings → send a test email; the error message says why. Usual causes: SES still in sandbox (step 3), `ALERT_EMAIL` domain not verified, or the role is missing `ses:SendEmail` (step 2). |
| Someone forgot their password | An admin deletes the account in **Users** and creates it again with a new password. If the only admin forgot theirs, see DEPLOY.md §8. |
| Logs for everything at once | `journalctl -u 'certwatch-*' -f` (`Ctrl+C` to stop). |

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
