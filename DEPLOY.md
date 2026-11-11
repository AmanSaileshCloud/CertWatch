# CERTWatch: production deployment (single EC2 server)

This guide puts the whole app on **one EC2 instance**: the API, the SSL checker, the website uptime worker, PostgreSQL, the frontend and Nginx. The steps are scripted (`deploy/setup.sh`, `deploy/update.sh`), and this guide covers the AWS pieces around them.

```
                          ┌───────────────────── EC2 t3.medium (ap-south-1) ─────────────────────┐
  Browser ──HTTPS:443──►  │ Nginx ── /assets, SPA ──► frontend/dist (static)                     │
                          │   └── /auth /admin /domains /checks /health /customers /groups       │
                          │       /monitors /alerts /alert-rules /settings ──► uvicorn :8000     │
                          │                                                     │                │
                          │ certwatch-api            (FastAPI, 2 workers) ──────┤                │
                          │ certwatch-checker-loop   (SSL certificates, 24x7) ──┤                │
                          │ certwatch-uptime         (websites, 24x7) ──────────┼──► PostgreSQL  │
                          │ certwatch-rollup.timer   (hourly summaries, 30-day cleanup)          │
                          │ certwatch-backup.timer   (nightly pg_dump ──► S3)                    │
                          └──────────────────────────────────────────────────────────────────────┘
  Route 53 health check ──► https://<domain>/health/ready ──► CloudWatch alarm ──► SNS ──► ops email/SMS
  Alerts to customers/engineers: Amazon SES (IAM role or SMTP), SSL alerts also via SNS
```

| Piece | How it runs |
|---|---|
| API | `uvicorn` under systemd (`certwatch-api`), on `127.0.0.1:8000` behind Nginx |
| SSL checker | `certwatch-checker-loop`: checks every stored domain every 300 s |
| Website checker | `certwatch-uptime`: checks each monitor at its own 1, 5 or 10-minute interval, sends DOWN/RECOVERED emails, and records a heartbeat |
| Database | PostgreSQL on the same server (or Amazon RDS), schema managed by Alembic migrations |
| Hourly job | `certwatch-rollup.timer`: summarises each finished hour and deletes raw results older than `UPTIME_RAW_RETENTION_DAYS` (30) |
| Backups | `certwatch-backup.timer`: nightly `pg_dump` (keeps 7 locally, copies to S3) |
| Users | Username/password JWT; accounts in `/opt/certwatch/data/auth_users.json` |

---

## 1. AWS prerequisites

**Instance:**
- Ubuntu Server **24.04 LTS** (22.04 also works)
- **t3.medium** (4 GB RAM); t3.small (2 GB) is the bare minimum
- region **ap-south-1**
- **30 GB gp3** disk

The old t2.micro (1 GB) can't run PostgreSQL and the workers together.

**Elastic IP:** attach one, so the address (and the DNS record and HTTPS) survives a stop and start.

**Security group:**
- **443** and **80** from anywhere. Port 80 is only for the certificate check and the redirect to HTTPS.
- **22** closed. Use **SSM Session Manager** to get a shell, or allow 22 from your office IP only.
- **8000 and 5432 stay closed.** The API and the database are only reachable from inside the server.

**IAM role** attached to the instance:
- Managed policy **`AmazonSSMManagedInstanceCore`** (shell access through Session Manager, and the private-domain SSL checks)
- This inline policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ses:SendEmail", "ses:SendRawEmail"], "Resource": "*" },
    { "Effect": "Allow", "Action": "sns:Publish",
      "Resource": "arn:aws:sns:ap-south-1:ACCOUNT_ID:ssl-alerts" },
    { "Effect": "Allow", "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::YOUR-BACKUP-BUCKET/certwatch/*" },
    { "Effect": "Allow", "Action": ["ssm:SendCommand", "ssm:GetCommandInvocation"], "Resource": "*" }
  ]
}
```
With the role attached, **no AWS keys go anywhere on the server**.

**Backup bucket:** create a private S3 bucket with:
- default encryption on
- *Block all public access* on
- a lifecycle rule that expires `certwatch/` objects after **30 days**

**DNS:** an A record such as `certwatch.yourcompany.com` pointing to the Elastic IP.

**Email (SES) for ap-south-1:**
1. Verify your sending domain, and add the **DKIM** CNAME records SES gives you.
2. Add an **SPF** record (`v=spf1 include:amazonses.com ~all`) and a **DMARC** record (`v=DMARC1; p=none; rua=mailto:you@yourcompany.com`).
3. Request **production access** to leave the SES sandbox. In the sandbox, SES only delivers to verified addresses.

Without these, DOWN alerts can land in spam.

For SSL alerts over SNS (optional), run once:
```bash
SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:ACCOUNT_ID:ssl-alerts ALERT_EMAIL=alerts@yourcompany.com \
AWS_REGION=ap-south-1 python -m backend.scripts.bootstrap
```

## 2. Install

On the instance:
```bash
sudo apt-get update -y && sudo apt-get install -y git
sudo git clone --branch v1.0.0 <YOUR_REPO_URL> /tmp/certwatch-src    # deploy a tagged release
cd /tmp/certwatch-src
sudo CERTWATCH_DOMAIN=certwatch.yourcompany.com CERTBOT_EMAIL=ops@yourcompany.com \
     bash deploy/setup.sh
```

`setup.sh` is safe to re-run. It does all of this:
- installs PostgreSQL, Python, Node and Nginx;
- adds 2 GB of swap and caps system logs at 1 GB;
- creates the `certwatch` system user;
- creates the database with a **generated password**;
- **generates every secret** (`AUTH_SECRET`, `SETTINGS_ENCRYPTION_KEY`, the database password, the first admin password);
- builds the frontend;
- applies the database migrations;
- installs and starts every service and timer;
- obtains the **HTTPS certificate** (with an automatic redirect from HTTP).

**Copy the admin password it prints at the end; it is shown only once.**

Without `CERTWATCH_DOMAIN`, the site is left on plain HTTP and the script warns you loudly. Don't let anyone log in until HTTPS is on, because passwords and tokens would cross the network unencrypted.

**Amazon RDS instead of a local database:**
1. Before running `setup.sh`, create `/etc/certwatch/certwatch.env` from `deploy/certwatch.env.example` (mode 640, group `certwatch`).
2. Set `DATABASE_URL` to the RDS endpoint in it.
3. Fill in `AUTH_SECRET`, `AUTH_BOOTSTRAP_PASSWORD` and `SETTINGS_ENCRYPTION_KEY` yourself (see the comments in the file).

`setup.sh` then skips the local PostgreSQL install.
Use RDS **PostgreSQL 16**, the same major version as Ubuntu 24.04's `pg_dump`. A newer server version would make the nightly backup fail unless you install the matching client.

## 3. Finish the configuration
```bash
sudo nano /etc/certwatch/certwatch.env
#   ALERT_EMAIL=alerts@yourcompany.com          (SES sender, must be verified)
#   SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:...    (optional, SSL alerts)
#   BACKUP_S3_BUCKET=your-backup-bucket
sudo systemctl restart certwatch-api certwatch-uptime certwatch-checker-loop
```
The alert recipients, reminder timing and SES SMTP credentials can also be managed later on the **Settings** page (admins only).

## 4. Verify
```bash
curl -s https://certwatch.yourcompany.com/health/ready
# {"status":"ok","uptime_worker_last_beat":"..."}
systemctl status certwatch-api certwatch-uptime certwatch-checker-loop
systemctl list-timers 'certwatch-*' 'certbot*'
```
Then log in as **admin**, change the password (Users panel), and follow the **go-live checklist** at the end of this file.

---

## 5. Monitoring the monitor (do not skip)

If the server, the database or the uptime worker stops, CertWatch can't alert anyone about it. Two alarms close that gap.

`/health/ready` answers **200** only when the database responds *and* the uptime worker finished a check cycle in the last 3 minutes. Otherwise it answers **503** and gives the reason.

**a) Route 53 health check, then an alarm email.** Run these from a machine with AWS admin access. Route 53 metrics live in **us-east-1**, so the SNS topic for the alarm must be there too.
```bash
aws sns create-topic --region us-east-1 --name certwatch-ops-alarms
aws sns subscribe --region us-east-1 --topic-arn arn:aws:sns:us-east-1:ACCOUNT_ID:certwatch-ops-alarms \
    --protocol email --notification-endpoint ops@yourcompany.com     # click the confirmation link

aws route53 create-health-check --caller-reference certwatch-$(date +%s) --health-check-config \
    Type=HTTPS,FullyQualifiedDomainName=certwatch.yourcompany.com,ResourcePath=/health/ready,RequestInterval=30,FailureThreshold=3
# note the returned HealthCheck Id

aws cloudwatch put-metric-alarm --region us-east-1 --alarm-name certwatch-not-ready \
    --namespace AWS/Route53 --metric-name HealthCheckStatus \
    --dimensions Name=HealthCheckId,Value=<HEALTH_CHECK_ID> \
    --statistic Minimum --period 60 --evaluation-periods 3 --threshold 1 \
    --comparison-operator LessThanThreshold --treat-missing-data breaching \
    --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:certwatch-ops-alarms \
    --ok-actions arn:aws:sns:us-east-1:ACCOUNT_ID:certwatch-ops-alarms
```
You also get an "OK" email when it recovers. Add an SMS subscription to the topic if you want a text message.

**b) EC2 auto-recovery** for hardware faults:
```bash
aws cloudwatch put-metric-alarm --region ap-south-1 --alarm-name certwatch-ec2-recover \
    --namespace AWS/EC2 --metric-name StatusCheckFailed_System \
    --dimensions Name=InstanceId,Value=<INSTANCE_ID> \
    --statistic Maximum --period 60 --evaluation-periods 2 --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --alarm-actions arn:aws:automate:ap-south-1:ec2:recover
```

**Test it before go-live:** run `sudo systemctl stop certwatch-uptime`. The alarm email should arrive within about 5 minutes. Then run `sudo systemctl start certwatch-uptime`, and the OK email follows.

## 6. Backups and restore

**Nightly:** `certwatch-backup.timer` runs at 02:30 UTC (08:00 IST). It writes `/opt/certwatch/backups/nightly-*.dump` (keeps the newest `BACKUP_KEEP_LOCAL`, default 7) and copies each dump to `s3://$BACKUP_S3_BUCKET/certwatch/<host>/`.

**Before every update:** `update.sh` writes `pre-update-*.dump` before it changes anything. Delete old ones now and then.

**Also back up `/opt/certwatch/data/auth_users.json`** (user accounts) **and `/etc/certwatch/certwatch.env`** (secrets) somewhere safe, such as a password manager. Without `SETTINGS_ENCRYPTION_KEY`, a saved SMTP password must be re-entered. Turning on an **EBS snapshot policy** (Data Lifecycle Manager, daily, keep 7) covers the whole disk as a second safety net.

Run a backup now:
```bash
sudo systemctl start certwatch-backup && journalctl -u certwatch-backup -n 20
```

**Restore drill** (do it once before go-live; it doesn't touch the live database):
```bash
sudo -u postgres createdb -O certwatch certwatch_restore_test
sudo -u certwatch pg_restore --no-owner \
     -d "postgresql://certwatch:<DB_PASSWORD>@localhost:5432/certwatch_restore_test" \
     /opt/certwatch/backups/<latest>.dump
sudo -u postgres psql certwatch_restore_test -c "SELECT count(*) FROM monitors; SELECT count(*) FROM domains;"
sudo -u postgres dropdb certwatch_restore_test
```

**Real restore** (replaces the live data):
```bash
sudo systemctl stop certwatch-uptime certwatch-checker-loop certwatch-api
source <(sudo grep ^DATABASE_URL= /etc/certwatch/certwatch.env)
sudo -u certwatch pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" <file.dump>
sudo systemctl start certwatch-api certwatch-uptime certwatch-checker-loop
```
To restore from S3, first run `aws s3 cp s3://<bucket>/certwatch/<host>/<file>.dump .`.

## 7. Updating

```bash
cd /tmp/certwatch-src && sudo git fetch --tags && sudo git checkout v1.0.1
sudo bash deploy/update.sh
curl -s https://certwatch.yourcompany.com/health/ready
```
`update.sh` runs these steps in order:
1. backs up the database;
2. syncs the code;
3. installs dependencies;
4. rebuilds the frontend;
5. **applies migrations**;
6. refreshes every systemd unit and the Nginx API routes (keeping the HTTPS config);
7. restarts the API, both checkers and the uptime worker.

If any step fails, it stops before the restart, and the running services keep the old code.

**Roll back:** check out the previous tag, restore the `pre-update-*.dump` (see §6), and run `update.sh` again.

**Upgrading an older SQLite or PM2 install:** run `sudo bash deploy/setup.sh` once instead of `update.sh`. It does this:
- adds PostgreSQL and `DATABASE_URL` to the existing env file;
- applies the migrations;
- **copies your SSL domains from the old SQLite file** (only once; a marker file prevents repeats);
- installs the systemd units.

Afterwards, stop the PM2 processes (`pm2 delete certwatch-api certwatch-checker`) so they don't run twice. `deploy/update-pm2.sh` remains for PM2 setups, but PM2 has no uptime worker unless you add one, so **systemd is the supported way**.

## 8. Day-2 operations
```bash
journalctl -u certwatch-api -f              # API
journalctl -u certwatch-uptime -f           # website checks + alert emails
journalctl -u certwatch-checker-loop -f     # SSL certificate checks
journalctl -u certwatch-rollup -n 20        # last hourly rollup
journalctl -u certwatch-backup -n 20        # last backup
sudo -u postgres psql certwatch             # database shell
```
- **Rotate `AUTH_SECRET`:** put a new `openssl rand -hex 32` in the env file and restart `certwatch-api`. Everyone must log in again.
- **The API refuses to start** if `AUTH_SECRET` is empty, too short or still a `CHANGE_ME` placeholder. Check `journalctl -u certwatch-api -n 30`.
- **SSL check interval:** run `sudo systemctl edit --full certwatch-checker-loop`, change `--interval 300`, then restart.
- **The only admin forgot their password:** the accounts live in `/opt/certwatch/data/auth_users.json`. When that file is empty or missing, the API creates `admin` again with `AUTH_BOOTSTRAP_PASSWORD` from the env file. This removes **all** user accounts; customers, websites and history aren't affected.
  ```bash
  sudo systemctl stop certwatch-api
  sudo mv /opt/certwatch/data/auth_users.json /opt/certwatch/data/auth_users.json.old
  sudo systemctl start certwatch-api
  sudo grep ^AUTH_BOOTSTRAP_PASSWORD= /etc/certwatch/certwatch.env   # log in as admin with this
  ```
  Then change the admin password and re-create the other users.
- **Disk space:** raw check results are kept for `UPTIME_RAW_RETENTION_DAYS` (30). Older history lives on in the hourly summary table.

## 9. Known limits (fine for launch; on the roadmap)
- Every logged-in user can see every customer, so **don't give customers logins yet**. Per-customer access arrives with the move of users into PostgreSQL.
- Checks run from one location (this server). A network problem near the server can look like a site outage. The "3 failures in a row" rule filters short blips.
- The login attempt limit and the test-alert cooldown are counted per API process (there are 2), so the effective limits are twice the configured values.

## 10. Go-live checklist
- [ ] t3.medium, Elastic IP, IAM role (§1), security group with 22, 8000 and 5432 closed
- [ ] `setup.sh` run with `CERTWATCH_DOMAIN`; `https://` works and `http://` redirects
- [ ] `ALERT_EMAIL`, `BACKUP_S3_BUCKET` set; SES domain verified with DKIM/SPF/DMARC; SES production access granted
- [ ] Admin password changed; other users created with the `user` role
- [ ] Test email from **Settings** arrives in the inbox (not spam)
- [ ] A test monitor for a URL that returns 503 gives a DOWN email after 3 checks, and a RECOVERED email once fixed
- [ ] `/health/ready` alarm tested by stopping `certwatch-uptime` (§5); EC2 recovery alarm created
- [ ] Backup ran, landed in S3, and the restore drill passed (§6)
- [ ] `auth_users.json` and `certwatch.env` backed up somewhere safe
- [ ] 2-week side-by-side run with Site24x7 started (same URLs in both; compare detected outages)

---

### Amazon Linux 2023 instead of Ubuntu?
`setup.sh` uses `apt`. On AL2023 the equivalents are:
- `dnf install -y python3 python3-pip nginx git rsync postgresql16-server`
- `postgresql-setup --initdb`
- Node via `dnf install -y nodejs20`

Nginx uses `/etc/nginx/conf.d/certwatch.conf` (no `sites-enabled`). The systemd units, env file and app code are identical.
