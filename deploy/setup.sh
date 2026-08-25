#!/usr/bin/env bash
#
# CERTWatch single-server setup for Ubuntu on EC2 (t2.micro).
# Idempotent — safe to re-run. Provisions everything: user, swap, venv, deps,
# frontend build, systemd services + timer, and Nginx.
#
#   sudo bash deploy/setup.sh
#
# Target OS: Ubuntu 22.04 / 24.04. For Amazon Linux 2023 see DEPLOY.md.
set -euo pipefail

APP_USER=certwatch
APP_HOME=/opt/certwatch
APP_DIR=$APP_HOME/app
DATA_DIR=$APP_HOME/data
VENV_DIR=$APP_HOME/venv
ENV_DIR=/etc/certwatch
ENV_FILE=$ENV_DIR/certwatch.env

# Repo root = parent of this script's directory.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo bash deploy/setup.sh" >&2
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip nginx git rsync curl openssl

echo "==> Ensuring 2G swap (t2.micro has only 1G RAM)"
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Creating service user + directories"
id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$ENV_DIR"

echo "==> Syncing app code to $APP_DIR"
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
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

  # Generate real secrets rather than leaving the CHANGE_ME placeholders in
  # place. The placeholders are published in this repo, so a deployment that
  # kept them would be signing JWTs with a key any reader already has — i.e.
  # anyone could forge an admin token. The API now refuses to start on a
  # placeholder secret, so this substitution is what keeps setup.sh working.
  GENERATED_SECRET="$(openssl rand -hex 32)"
  GENERATED_ADMIN_PW="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  sed -i \
    -e "s|^AUTH_SECRET=.*|AUTH_SECRET=${GENERATED_SECRET}|" \
    -e "s|^AUTH_BOOTSTRAP_PASSWORD=.*|AUTH_BOOTSTRAP_PASSWORD=${GENERATED_ADMIN_PW}|" \
    "$ENV_FILE"

  GENERATED_CREDS=1
  echo "    Created $ENV_FILE with freshly generated AUTH_SECRET + admin password."
  echo "    Still to edit by hand: SNS_TOPIC_ARN and ALERT_EMAIL."
else
  GENERATED_CREDS=0
  echo "    $ENV_FILE already exists — left untouched."
fi

echo "==> Installing systemd units"
cp "$APP_DIR/deploy/certwatch-api.service"          /etc/systemd/system/
cp "$APP_DIR/deploy/certwatch-checker.service"      /etc/systemd/system/
cp "$APP_DIR/deploy/certwatch-checker.timer"        /etc/systemd/system/
cp "$APP_DIR/deploy/certwatch-checker-loop.service" /etc/systemd/system/

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
fi
ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/certwatch
rm -f /etc/nginx/sites-enabled/default

echo "==> Fixing ownership"
chown -R "$APP_USER:$APP_USER" "$APP_HOME"
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> Enabling and starting services"
systemctl daemon-reload
systemctl enable --now certwatch-api.service
# Continuous 24x7 checker. (The periodic .timer is the alternative — enable one,
# not both. Disable the timer here so re-runs don't leave both active.)
systemctl disable --now certwatch-checker.timer 2>/dev/null || true
systemctl enable --now certwatch-checker-loop.service
nginx -t
systemctl restart nginx

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
echo ""
echo "  IMPORTANT — this listener is plain HTTP. Passwords and session tokens"
echo "  would cross the network in cleartext. Before exposing it to anyone,"
echo "  point a domain at this box and enable TLS (see DEPLOY.md):"
echo "      sudo apt-get install -y certbot python3-certbot-nginx"
echo "      sudo certbot --nginx -d <your-domain> --redirect"
echo ""
echo "  Next: set SNS_TOPIC_ARN + ALERT_EMAIL in $ENV_FILE,"
echo "        then  sudo systemctl restart certwatch-api"
echo "  Logs: journalctl -u certwatch-api -f"
