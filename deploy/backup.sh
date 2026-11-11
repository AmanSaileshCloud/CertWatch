#!/usr/bin/env bash
#
# Nightly CertWatch database backup (run by certwatch-backup.timer as the
# certwatch user, with /etc/certwatch/certwatch.env loaded by systemd).
#
#   1. pg_dump the database to /opt/certwatch/backups (compressed custom format)
#   2. keep the newest BACKUP_KEEP_LOCAL nightly files (default 7)
#   3. if BACKUP_S3_BUCKET is set, copy the new file to S3 using the instance role
#
# Restore (see DEPLOY.md):  pg_restore --clean --if-exists -d "$DATABASE_URL" <file>
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is not set}"
BACKUP_DIR=${BACKUP_DIR:-/opt/certwatch/backups}
KEEP=${BACKUP_KEEP_LOCAL:-7}
PYTHON=${PYTHON:-/opt/certwatch/venv/bin/python}

mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/nightly-$(date -u +%Y%m%dT%H%M%SZ).dump"

echo "Dumping database to $file"
pg_dump --format=custom --dbname="$DATABASE_URL" --file="$file.partial"
mv "$file.partial" "$file"   # only complete dumps ever carry the final name

# Prune old nightly dumps (pre-update dumps are left for you to remove).
ls -1t "$BACKUP_DIR"/nightly-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  key="certwatch/$(hostname)/$(basename "$file")"
  echo "Uploading to s3://$BACKUP_S3_BUCKET/$key"
  "$PYTHON" - "$file" "$BACKUP_S3_BUCKET" "$key" <<'PY'
import sys

import boto3

path, bucket, key = sys.argv[1:4]
boto3.client("s3").upload_file(
    path, bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"}
)
PY
fi
echo "Backup complete: $file ($(du -h "$file" | cut -f1))"
