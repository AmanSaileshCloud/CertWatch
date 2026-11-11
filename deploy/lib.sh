# shellcheck shell=bash
#
# Shared steps for deploy/setup.sh and deploy/update.sh. Sourced, not run.
# Expects APP_USER, APP_HOME, APP_DIR, DATA_DIR, VENV_DIR, ENV_FILE to be set.

BACKUP_DIR=${BACKUP_DIR:-/opt/certwatch/backups}

# Every systemd unit CertWatch ships. Copied on setup and on every update so a
# new or changed unit never gets left behind.
CERTWATCH_UNITS=(
  certwatch-api.service
  certwatch-checker.service
  certwatch-checker.timer
  certwatch-checker-loop.service
  certwatch-uptime.service
  certwatch-rollup.service
  certwatch-rollup.timer
  certwatch-backup.service
  certwatch-backup.timer
)

# Print one KEY's value from the env file (last occurrence wins, like systemd).
env_value() {
  local key=$1
  grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-
}

# Set KEY=VALUE in the env file: replace the line if present, else append it.
set_env_value() {
  local key=$1 value=$2
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
  fi
}

# Host part of DATABASE_URL (postgresql://user:pw@HOST:port/db).
db_host() {
  env_value DATABASE_URL | sed -E 's|^[a-z+]+://[^@]*@([^:/]+).*|\1|'
}

db_is_local() {
  case "$(db_host)" in
    localhost|127.0.0.1|::1) return 0 ;;
    *) return 1 ;;
  esac
}

# Run a command as the app user with the production DATABASE_URL, from the app dir.
run_as_app() {
  local url
  url="$(env_value DATABASE_URL)"
  runuser -u "$APP_USER" -- env DATABASE_URL="$url" \
    bash -c 'cd "$1" && shift && exec "$@"' _ "$APP_DIR" "$@"
}

# Create the local PostgreSQL role + database from DATABASE_URL (idempotent; the
# role's password is (re)set to match the env file, so the two never drift).
provision_local_postgres() {
  local url user password database
  url="$(env_value DATABASE_URL)"
  user="$(echo "$url" | sed -E 's|^[a-z+]+://([^:]+):.*|\1|')"
  password="$(echo "$url" | sed -E 's|^[a-z+]+://[^:]+:([^@]*)@.*|\1|')"
  database="$(echo "$url" | sed -E 's|.*/([^/?]+)(\?.*)?$|\1|')"

  if [[ "$password" == *CHANGE_ME* || -z "$password" ]]; then
    echo "DATABASE_URL in $ENV_FILE still has a placeholder password." >&2
    return 1
  fi

  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q \
    -v user="$user" -v password="$password" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'user', :'password') \gexec
SQL
  if ! runuser -u postgres -- psql -tAc \
      "SELECT 1 FROM pg_database WHERE datname = '${database}'" | grep -q 1; then
    runuser -u postgres -- createdb -O "$user" "$database"
  fi
}

# Small-server tuning (Ubuntu's postgresql.conf includes conf.d/ by default).
tune_local_postgres() {
  local conf_dir
  conf_dir="$(ls -d /etc/postgresql/*/main/conf.d 2>/dev/null | sort -V | tail -n 1)"
  [ -n "$conf_dir" ] || return 0
  cat > "$conf_dir/certwatch.conf" <<'CONF'
# CertWatch: tuned for a 2–4 GB single-server install.
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 8MB
maintenance_work_mem = 128MB
max_connections = 60
CONF
  systemctl restart postgresql
}

# Apply database migrations (safe to run any number of times).
run_migrations() {
  echo "==> Applying database migrations"
  run_as_app "$VENV_DIR/bin/alembic" upgrade head
}

# One-time copy of SSL domains from an older SQLite-based install.
migrate_legacy_sqlite() {
  local sqlite marker="$DATA_DIR/.sqlite-migrated"
  sqlite="$(env_value SQLITE_PATH)"
  if [ -n "$sqlite" ] && [ -f "$sqlite" ] && [ ! -f "$marker" ]; then
    echo "==> Copying domains from the old SQLite database ($sqlite)"
    run_as_app "$VENV_DIR/bin/python" -m backend.scripts.migrate_sqlite_to_postgres \
      --sqlite "$sqlite"
    touch "$marker"
    chown "$APP_USER:$APP_USER" "$marker"
  fi
}

# Database dump before an update touches anything (custom format, compressed).
pre_update_backup() {
  mkdir -p "$BACKUP_DIR"
  chown "$APP_USER:$APP_USER" "$BACKUP_DIR"
  local file="$BACKUP_DIR/pre-update-$(date -u +%Y%m%dT%H%M%SZ).dump"
  echo "==> Backing up the database to $file"
  run_as_app pg_dump --format=custom --dbname="$(env_value DATABASE_URL)" --file="$file"
}

install_units() {
  echo "==> Installing systemd units"
  local unit
  for unit in "${CERTWATCH_UNITS[@]}"; do
    cp "$APP_DIR/deploy/$unit" /etc/systemd/system/
  done
  systemctl daemon-reload
}
