#!/usr/bin/env bash
#
# CERTWatch single-server setup for Ubuntu 22.04 / 24.04 on EC2.
# Idempotent — safe to re-run. Provisions everything: PostgreSQL, user, swap,
# venv, deps, frontend build, database migrations, systemd services + timers,
# Nginx, and (when CERTWATCH_DOMAIN is set) HTTPS.
#
#   sudo bash deploy/setup.sh
#   sudo CERTWATCH_DOMAIN=certwatch.example.com CERTBOT_EMAIL=ops@example.com \
#        bash deploy/setup.sh          # also obtains the HTTPS certificate
#
# To use Amazon RDS instead of a local PostgreSQL, create
# /etc/certwatch/certwatch.env first with DATABASE_URL pointing at RDS.
set -euo pipefail

APP_USER=certwatch
APP_HOME=/opt/certwatch
APP_DIR=$APP_HOME/app
DATA_DIR=$APP_HOME/data
VENV_DIR=$APP_HOME/venv
ENV_DIR=/etc/certwatch
ENV_FILE=$ENV_DIR/certwatch.env
CERTWATCH_DOMAIN=${CERTWATCH_DOMAIN:-}
CERTBOT_EMAIL=${CERTBOT_EMAIL:-}

# Repo root = parent of this script's directory.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$SRC_DIR/deploy/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo bash deploy/setup.sh" >&2
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip nginx git rsync curl openssl \
  postgresql postgresql-client

echo "==> Ensuring 2G swap"
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Keeping system logs under 1 GB"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=1G\n' > /etc/systemd/journald.conf.d/certwatch.conf
systemctl restart systemd-journald

echo "==> Creating service user + directories"
id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$ENV_DIR" "$BACKUP_DIR"

echo "==> Syncing app code to $APP_DIR"
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '.env' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' \
  "$SRC_DIR"/ "$APP_DIR"/

echo "==> Python venv + dependencies"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$APP_DIR"

echo "==> Building frontend (same-origin: VITE_API_URL empty)"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
pushd "$APP_DIR/frontend" >/dev/null
npm ci
VITE_API_URL="" npm run build
popd >/dev/null

echo "==> Installing env file"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/deploy/certwatch.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  # Generate real secrets rather than leaving the CHANGE_ME placeholders in
  # place. The placeholders are published in this repo, so a deployment that
  # kept them would be signing JWTs with a key any reader already has — i.e.
  # anyone could forge an admin token. The API refuses to start on a
  # placeholder secret, so this substitution is what keeps setup.sh working.
  GENERATED_SECRET="$(openssl rand -hex 32)"
  GENERATED_ADMIN_PW="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  # Hex only, so the password is safe inside a URL without escaping.
  GENERATED_DB_PW="$(openssl rand -hex 24)"
  # Encrypts the SES SMTP password saved on the Settings page (losing it only
  # means re-entering that password).
  GENERATED_SETTINGS_KEY="$("$VENV_DIR/bin/python" -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
  sed -i \
    -e "s|^AUTH_SECRET=.*|AUTH_SECRET=${GENERATED_SECRET}|" \
    -e "s|^AUTH_BOOTSTRAP_PASSWORD=.*|AUTH_BOOTSTRAP_PASSWORD=${GENERATED_ADMIN_PW}|" \
    -e "s|^SETTINGS_ENCRYPTION_KEY=.*|SETTINGS_ENCRYPTION_KEY=${GENERATED_SETTINGS_KEY}|" \
    -e "s|CHANGE_ME_DB_PASSWORD|${GENERATED_DB_PW}|" \
    "$ENV_FILE"

  GENERATED_CREDS=1
  echo "    Created $ENV_FILE with generated AUTH_SECRET, SETTINGS_ENCRYPTION_KEY,"
  echo "    database password and admin password."
else
  GENERATED_CREDS=0
  echo "    $ENV_FILE already exists — left untouched."
  if ! grep -qE '^DATABASE_URL=' "$ENV_FILE"; then
    # Upgrading a pre-PostgreSQL (SQLite) install: add the database settings.
    echo "    Adding PostgreSQL settings to the existing env file."
    set_env_value STORAGE postgres
    set_env_value DATABASE_URL "postgresql://certwatch:$(openssl rand -hex 24)@localhost:5432/certwatch"
  fi
fi

echo "==> Fixing ownership"
chown -R "$APP_USER:$APP_USER" "$APP_HOME"
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

if db_is_local; then
  echo "==> Setting up the local PostgreSQL database"
  systemctl enable --now postgresql
  tune_local_postgres
  provision_local_postgres
else
  echo "==> Using the external database at $(db_host) (not installing a local one)"
fi

run_migrations
migrate_legacy_sqlite

install_units

echo "==> Installing Nginx site"
mkdir -p /etc/nginx/snippets
cp "$APP_DIR/deploy/security-headers.conf" /etc/nginx/snippets/certwatch-security-headers.conf

NGINX_SITE=/etc/nginx/sites-available/certwatch
# certbot --nginx rewrites this file in place to add the TLS server block and
# the HTTP->HTTPS redirect. setup.sh is advertised as re-runnable, so copying
# the plain-HTTP template back over it would silently tear down HTTPS.
if [ -f "$NGINX_SITE" ] && grep -q 'ssl_certificate' "$NGINX_SITE"; then
  echo "    $NGINX_SITE has TLS configured — left untouched."
  echo "    (Delete it and re-run if you need to reset to the stock config.)"
else
  cp "$APP_DIR/deploy/nginx.conf" "$NGINX_SITE"
  if [ -n "$CERTWATCH_DOMAIN" ]; then
    sed -i "s/server_name _;/server_name ${CERTWATCH_DOMAIN};/" "$NGINX_SITE"
  fi
fi
ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/certwatch
rm -f /etc/nginx/sites-enabled/default

echo "==> Enabling and starting services"
systemctl enable --now certwatch-api.service
# Continuous 24x7 checker. (The periodic .timer is the alternative — enable one,
# not both. Disable the timer here so re-runs don't leave both active.)
systemctl disable --now certwatch-checker.timer 2>/dev/null || true
systemctl enable --now certwatch-checker-loop.service
systemctl enable --now certwatch-uptime.service
# Hourly uptime summaries + raw-result cleanup, and the nightly database backup.
systemctl enable --now certwatch-rollup.timer
systemctl enable --now certwatch-backup.timer
nginx -t
systemctl restart nginx

HTTPS_ON=0
if grep -q 'ssl_certificate' "$NGINX_SITE"; then
  HTTPS_ON=1
elif [ -n "$CERTWATCH_DOMAIN" ]; then
  echo "==> Obtaining an HTTPS certificate for $CERTWATCH_DOMAIN"
  if [ -z "$CERTBOT_EMAIL" ]; then
    echo "CERTBOT_EMAIL is required with CERTWATCH_DOMAIN (Let's Encrypt expiry notices)." >&2
    exit 1
  fi
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$CERTWATCH_DOMAIN" --redirect --agree-tos -m "$CERTBOT_EMAIL" \
    --non-interactive
  HTTPS_ON=1
fi
if [ -n "$CERTWATCH_DOMAIN" ] && [ -z "$(env_value CERTWATCH_PUBLIC_URL)" ]; then
  set_env_value CERTWATCH_PUBLIC_URL "https://${CERTWATCH_DOMAIN}"
  systemctl restart certwatch-api.service certwatch-uptime.service
fi

echo ""
echo "✓ CERTWatch is up."
if [ "$GENERATED_CREDS" -eq 1 ]; then
  echo ""
  echo "  ── First-run admin credentials (shown once) ──────────────────"
  echo "     username: admin"
  echo "     password: $GENERATED_ADMIN_PW"
  echo "     Log in and change it from the Users panel."
  echo "  ──────────────────────────────────────────────────────────────"
fi
if [ "$HTTPS_ON" -eq 0 ]; then
  echo ""
  echo "  !! NOT SAFE YET — this site is plain HTTP. Passwords and session tokens"
  echo "  !! would cross the network in cleartext. Point a domain at this server"
  echo "  !! and re-run with HTTPS:"
  echo "       sudo CERTWATCH_DOMAIN=<your-domain> CERTBOT_EMAIL=<you@company> bash deploy/setup.sh"
fi
echo ""
echo "  Next: set ALERT_EMAIL (and SNS_TOPIC_ARN / BACKUP_S3_BUCKET) in $ENV_FILE,"
echo "        then  sudo systemctl restart certwatch-api certwatch-uptime"
echo "  Check: curl -s http://localhost/health/ready"
echo "  Logs:  journalctl -u certwatch-api -u certwatch-uptime -f"
