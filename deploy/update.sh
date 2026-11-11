#!/usr/bin/env bash
#
# Redeploy CERTWatch after a code change. Run from an updated checkout:
#   sudo bash deploy/update.sh
#
# Backs up the database, syncs code, reinstalls deps, rebuilds the frontend,
# applies database migrations, refreshes systemd units and restarts services.
# If any step fails the script stops, and the services keep running the old code.
set -euo pipefail

APP_USER=certwatch
APP_HOME=/opt/certwatch
APP_DIR=$APP_HOME/app
DATA_DIR=$APP_HOME/data
VENV_DIR=$APP_HOME/venv
ENV_FILE=/etc/certwatch/certwatch.env
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
source "$SRC_DIR/deploy/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo bash deploy/update.sh" >&2
  exit 1
fi
if ! grep -qE '^DATABASE_URL=' "$ENV_FILE"; then
  echo "$ENV_FILE has no DATABASE_URL — this server predates PostgreSQL." >&2
  echo "Run  sudo bash deploy/setup.sh  once instead; it upgrades the install." >&2
  exit 1
fi

# Safety net first: if a migration goes wrong, restore with
#   pg_restore --clean --if-exists -d "$DATABASE_URL" <file>
pre_update_backup

echo "==> Syncing code"
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '.env' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' \
  "$SRC_DIR"/ "$APP_DIR"/

echo "==> Updating Python deps"
"$VENV_DIR/bin/pip" install -e "$APP_DIR"

echo "==> Rebuilding frontend"
pushd "$APP_DIR/frontend" >/dev/null
npm ci
VITE_API_URL="" npm run build
popd >/dev/null

chown -R "$APP_USER:$APP_USER" "$APP_HOME"

run_migrations

install_units

echo "==> Refreshing Nginx"
# Security headers are `include`d by the site config — install them first, or
# `nginx -t` below fails on a missing include.
mkdir -p /etc/nginx/snippets
cp "$APP_DIR/deploy/security-headers.conf" /etc/nginx/snippets/certwatch-security-headers.conf

NGINX_SITE=/etc/nginx/sites-available/certwatch
if [ -f "$NGINX_SITE" ] && grep -q 'ssl_certificate' "$NGINX_SITE"; then
  # certbot added the TLS block to this file, so it can't simply be replaced.
  # Only bring its API route list up to date with the shipped template.
  ROUTES="$(grep -m1 -E '^[[:space:]]*location ~ \^/\(' "$APP_DIR/deploy/nginx.conf")"
  awk -v new="$ROUTES" \
    '/^[[:space:]]*location ~ \^\/\(/ { print new; next } { print }' \
    "$NGINX_SITE" > "$NGINX_SITE.tmp" && mv "$NGINX_SITE.tmp" "$NGINX_SITE"
  echo "    $NGINX_SITE has TLS configured — API routes refreshed, rest left untouched."
else
  cp "$APP_DIR/deploy/nginx.conf" "$NGINX_SITE"
fi

echo "==> Restarting services"
systemctl restart certwatch-api.service
# Restart the 24x7 SSL checker if it's the active one (the timer-based oneshot
# picks up new code automatically on its next scheduled run).
if systemctl is-active --quiet certwatch-checker-loop.service; then
  systemctl restart certwatch-checker-loop.service
fi
systemctl enable certwatch-uptime.service >/dev/null
systemctl restart certwatch-uptime.service
systemctl enable --now certwatch-rollup.timer certwatch-backup.timer
nginx -t && systemctl reload nginx

echo "✓ Update complete. Check: curl -s http://localhost/health/ready"
echo "  Logs: journalctl -u certwatch-api -u certwatch-uptime -f"
