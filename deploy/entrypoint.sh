#!/bin/sh
set -eu

: "${R2_PATH:=kartonkerels}"
export R2_PATH

if [ -z "${R2_BUCKET:-}" ] || [ -z "${R2_ACCOUNT_ID:-}" ] || [ -z "${R2_ACCESS_KEY_ID:-}" ] || [ -z "${R2_SECRET_ACCESS_KEY:-}" ]; then
  if [ "${ALLOW_EPHEMERAL_DB:-}" = "1" ]; then
    echo "WARNING: R2 not configured, data will be lost when the container stops." >&2
    exec python app.py
  fi
  echo "ERROR: R2_BUCKET, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY must be set." >&2
  exit 1
fi

# The container disk is wiped on every restart: pull the latest database from R2 first.
litestream restore -if-db-not-exists -if-replica-exists "$DATABASE_PATH"

# Very first start (nothing replicated yet): import seed/kartonkerels.db from the bucket if you uploaded one.
if [ ! -f "$DATABASE_PATH" ]; then
  python /app/deploy/seed_from_r2.py
fi

# Stream every change back to R2 while the app runs.
exec litestream replicate -exec "python app.py"
