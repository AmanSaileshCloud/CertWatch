#!/usr/bin/env bash
#
# Redeploy CERTWatch when it runs under PM2 (not systemd).
#   sudo bash deploy/update-pm2.sh              # backend-only (fast)
#   sudo bash deploy/update-pm2.sh --frontend   # also rebuild the React app
#
# Run it from an updated checkout — i.e. `git pull` first. Syncs the code into
# /opt/certwatch/app, updates Python deps, and restarts the PM2 processes.
set -euo pipefail

APP_HOME=/opt/certwatch
APP_DIR=$APP_HOME/app
VENV_DIR=$APP_HOME/venv
ENV_FILE=/etc/certwatch/certwatch.env
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR=$APP_HOME/backups

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo bash deploy/update-pm2.sh" >&2
  exit 1
fi

BUILD_FRONTEND=false
[ "${1:-}" = "--frontend" ] && BUILD_FRONTEND=true

DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
if [ -z "$DATABASE_URL" ]; then
  echo "$ENV_FILE has no DATABASE_URL — CertWatch now needs PostgreSQL." >&2
  echo "Run  sudo bash deploy/setup.sh  once to install it and switch to systemd." >&2
  exit 1
fi

echo "==> Backing up the database"
mkdir -p "$BACKUP_DIR"
pg_dump --format=custom --dbname="$DATABASE_URL"   --file="$BACKUP_DIR/pre-update-$(date -u +%Y%m%dT%H%M%SZ).dump"

echo "==> Syncing code to $APP_DIR"
# Exclude .env so the symlink the app loads its config from is never deleted.
rsync -a --delete \
  --exclude '.git' --exclude '.env' --exclude 'node_modules' --exclude '.venv' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' \
  "$SRC_DIR"/ "$APP_DIR"/

# PM2 has no EnvironmentFile; the app auto-loads a .env from its working dir, so
# keep that pointed at the real env file (idempotent).
ln -sf "$ENV_FILE" "$APP_DIR/.env"

echo "==> Updating Python deps"
"$VENV_DIR/bin/pip" install -e "$APP_DIR" >/dev/null

if [ "$BUILD_FRONTEND" = true ]; then
  echo "==> Rebuilding frontend"
  ( cd "$APP_DIR/frontend" && npm ci && VITE_API_URL="" npm run build )
else
  echo "==> Skipping frontend rebuild (pass --frontend if the UI changed)"
fi

echo "==> Applying database migrations"
( cd "$APP_DIR" && DATABASE_URL="$DATABASE_URL" "$VENV_DIR/bin/alembic" upgrade head )

echo "==> Restarting PM2 processes"
pm2 restart certwatch-api certwatch-checker
if pm2 describe certwatch-uptime >/dev/null 2>&1; then
  pm2 restart certwatch-uptime
else
  echo "    !! No certwatch-uptime process in PM2 — websites are NOT being monitored."
  echo "    !! Recommended: switch to systemd with  sudo bash deploy/setup.sh"
fi

echo "✓ Update complete. Logs: pm2 logs certwatch-api"
