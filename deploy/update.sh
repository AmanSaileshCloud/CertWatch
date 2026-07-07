#!/usr/bin/env bash
#
# Redeploy CERTWatch after a code change. Run from an updated checkout:
#   sudo bash deploy/update.sh
#
# Syncs code, reinstalls deps, rebuilds the frontend, and restarts services.
set -euo pipefail

APP_USER=certwatch
APP_HOME=/opt/certwatch
APP_DIR=$APP_HOME/app
VENV_DIR=$APP_HOME/venv
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo bash deploy/update.sh" >&2
  exit 1
fi

echo "==> Syncing code"
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' \
  "$SRC_DIR"/ "$APP_DIR"/

echo "==> Updating Python deps"
"$VENV_DIR/bin/pip" install -e "$APP_DIR"

echo "==> Rebuilding frontend"
pushd "$APP_DIR/frontend" >/dev/null
npm ci
VITE_API_URL="" npm run build
popd >/dev/null

echo "==> Refreshing systemd units + Nginx"
cp "$APP_DIR/deploy/certwatch-api.service"     /etc/systemd/system/
cp "$APP_DIR/deploy/certwatch-checker.service" /etc/systemd/system/
cp "$APP_DIR/deploy/certwatch-checker.timer"   /etc/systemd/system/
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/certwatch

chown -R "$APP_USER:$APP_USER" "$APP_HOME"

systemctl daemon-reload
systemctl restart certwatch-api.service
nginx -t && systemctl reload nginx

echo "✓ Update complete. Logs: journalctl -u certwatch-api -f"
