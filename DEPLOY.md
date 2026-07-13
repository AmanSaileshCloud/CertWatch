# CERTWatch — Single-Server Deployment (EC2 t2.micro)

This deploys the whole app — API, checker, and frontend — onto **one EC2
instance**. No Lambda, API Gateway, DynamoDB, CloudFront, or S3.

```
                        ┌──────────────────── EC2 t2.micro ───────────────────┐
   Browser  ──HTTP:80─► │  Nginx ──/assets, SPA──►  frontend/dist (static)     │
                        │    └──/auth /domains /checks /admin /health──┐       │
                        │                                              ▼       │
                        │                       uvicorn :8000  (systemd svc)   │
                        │                              │                       │
                        │                              ▼                       │
                        │                    SQLite  /opt/certwatch/data       │
                        │  checker-loop (24x7) ─► run_checker ──► SES/SNS       │
                        └──────────────────────────────────────────────────────┘
```

| Piece | How it runs |
|-------|-------------|
| API | `uvicorn` under **systemd** (`certwatch-api.service`), bound to `127.0.0.1:8000` |
| Checker | **`certwatch-checker-loop.service`** — 24×7 `run_checker --interval 300`, auto-restarts (periodic `.timer` is the alternative) |
| Storage | **SQLite** file at `/opt/certwatch/data/certwatch.db` |
| Alerts | **AWS SNS + SES** via the EC2 IAM role (per manager) |
| Login / users | **Username/password JWT** — self-contained, `auth_users.json` on the box |
| Digest | On-demand **HTML download** from the dashboard (no email needed) |
| Frontend | Static build served by **Nginx**; same-origin, no CORS |

---

## 1. Launch the instance

- **AMI:** Ubuntu Server 22.04 or 24.04 LTS
- **Type:** t2.micro
- **Storage:** 8–16 GB gp3
- **Security group:**
  - TCP **22** (SSH) — from **your IP only**
  - TCP **80** (HTTP) — from anywhere (or your office range)
  - Leave **8000 closed** — uvicorn is internal, reached only via Nginx

## 2. Attach an IAM role (for SES/SNS alerts)

Create an IAM role for EC2 and attach it to the instance. Minimum policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "sns:Publish",
      "Resource": "arn:aws:sns:us-east-1:ACCOUNT_ID:ssl-alerts" },
    { "Effect": "Allow", "Action": ["ses:SendEmail", "ses:SendRawEmail"],
      "Resource": "*" }
  ]
}
```

With the role attached, **no AWS keys go in the env file** — boto3 reads
temporary credentials from instance metadata automatically.

## 3. Create the SNS topic + verify SES sender (once)

From your laptop or the instance (with the role/keys and the repo installed):

```bash
SNS_TOPIC_ARN=arn:aws:sns:us-east-1:ACCOUNT_ID:ssl-alerts \
ALERT_EMAIL=alerts@yourcompany.com \
AWS_REGION=us-east-1 \
python -m backend.scripts.bootstrap
```

This creates the `ssl-alerts` topic and requests SES verification for the
sender. **Confirm the SNS email subscription** and **click the SES verification
link** that AWS emails you. To alert arbitrary recipients (not just verified
ones), request SES production access to leave the sandbox.

## 4. Provision the server

```bash
# on the instance
sudo apt-get update -y && sudo apt-get install -y git
sudo git clone <YOUR_REPO_URL> /tmp/certwatch-src
cd /tmp/certwatch-src
sudo bash deploy/setup.sh
```

`setup.sh` is idempotent and does everything: 2 GB swap, `certwatch` system
user, Python venv + deps, frontend build, systemd units + timer, and Nginx.

## 5. Fill in secrets and restart

```bash
sudo nano /etc/certwatch/certwatch.env      # set the CHANGE_ME values
sudo systemctl restart certwatch-api
```

At minimum set: `AUTH_SECRET` (`openssl rand -hex 32`), `AUTH_BOOTSTRAP_PASSWORD`
(the first admin password), `SNS_TOPIC_ARN`, and `ALERT_EMAIL`.

> The API **refuses to start** with `AUTH_ENABLED=true` while `AUTH_SECRET` is
> the built-in default — a misconfigured deploy fails loudly instead of shipping
> a known secret.

## 6. Verify

```bash
curl -s http://localhost/health              # {"status":"ok"}
systemctl status certwatch-api               # active (running)
systemctl status certwatch-checker-loop      # active (running) — the 24x7 checker
```

Open `http://<instance-public-ip>/` in a browser — the CERTWatch login appears.
Log in as **admin** with the `AUTH_BOOTSTRAP_PASSWORD` you set; the admin sees the
Digest button and other admin controls.

---

## Day-2 operations

**Logs**
```bash
journalctl -u certwatch-api -f                 # API
journalctl -u certwatch-checker-loop -f        # live 24x7 checker passes
```

**Change how often it checks** — edit the interval (seconds) and restart:
```bash
sudo systemctl edit --full certwatch-checker-loop   # change --interval 300
sudo systemctl restart certwatch-checker-loop
```

**Prefer periodic instead of 24x7?** Swap to the timer:
```bash
sudo systemctl disable --now certwatch-checker-loop
sudo systemctl enable --now certwatch-checker.timer
```

**Deploy a code update**
```bash
cd /path/to/updated/checkout && git pull
sudo bash deploy/update.sh                # systemd: sync, rebuild, restart
```
If you run under **PM2** instead of systemd, use the PM2 variant (it restarts
the PM2 processes, not the systemd units):
```bash
sudo bash deploy/update-pm2.sh            # backend-only (fast)
sudo bash deploy/update-pm2.sh --frontend # also rebuild the React UI
```

**Back up the data** (everything lives in one directory)
```bash
sudo cp -r /opt/certwatch/data ~/certwatch-backup-$(date +%F)
```
`certwatch.db` is the whole database; `auth_users.json` holds the user accounts.

---

## Adding HTTPS later (when you have a domain)

1. Point an A record at the instance's public IP (or an Elastic IP).
2. Open TCP **443** in the security group.
3. Install a cert:
   ```bash
   sudo apt-get install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d certwatch.yourcompany.com
   ```
   Certbot edits the Nginx site and sets up auto-renewal — fitting, since this
   app monitors exactly that expiry. HTTPS is strongly recommended before real
   use: login credentials and JWTs ride over the wire and should not travel over
   plain HTTP on the public internet.

---

## Amazon Linux 2023 instead of Ubuntu?

`setup.sh` uses `apt`. On AL2023 the equivalents are: `dnf install -y python3
python3-pip nginx git rsync`, Node via `dnf install -y nodejs20`, and Nginx
uses `/etc/nginx/conf.d/certwatch.conf` (no `sites-enabled`). The systemd units,
env file, and app code are identical.

---

## What changed from the serverless design

| Serverless (retired) | Single server |
|----------------------|---------------|
| Lambda + Mangum + API Gateway | uvicorn + systemd + Nginx |
| EventBridge schedule | systemd service (24×7 loop) or timer |
| DynamoDB | SQLite (`adapters/storage/sqlite.py`) |
| S3 + CloudFront | Nginx static files |
| SNS + SES | **unchanged** — still used for alerts |
| Auth | **unchanged** — username/password JWT, `auth_users.json` on the box |

The serverless artifacts (`template.yaml`, `samconfig.toml`, `Makefile`, the
Mangum/Lambda handlers) have been removed — this is now a single-server codebase.
