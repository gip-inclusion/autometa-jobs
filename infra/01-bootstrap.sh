#!/usr/bin/env bash
# Create the S3 bucket and the Postgres database.
# Reuses existing proto-db instance and existing nova-container-registry.

source "$(dirname "$0")/lib.sh"
require_scw

info "Project: $PROJECT_ID"
info "Region : $REGION"
info "Bucket : $BUCKET_NAME"
info "DB     : $DB_NAME on instance $RDB_INSTANCE_ID"

# --- 1. Object Storage bucket ----------------------------------------------
if scw object bucket get "$BUCKET_NAME" region="$REGION" >/dev/null 2>&1; then
  ok "bucket $BUCKET_NAME already exists"
else
  if confirm "Create bucket $BUCKET_NAME in $REGION?"; then
    scw object bucket create "$BUCKET_NAME" region="$REGION" >/dev/null
    ok "bucket $BUCKET_NAME created"
  else
    warn "bucket creation skipped"
  fi
fi

# --- 2. Postgres database --------------------------------------------------
existing_db=$(scw rdb database list instance-id="$RDB_INSTANCE_ID" region="$REGION" -o json \
  | python3 -c "import sys, json; print('|'.join(d['name'] for d in json.load(sys.stdin)))")
if [[ "|$existing_db|" == *"|$DB_NAME|"* ]]; then
  ok "database $DB_NAME already exists on $RDB_INSTANCE_ID"
else
  if confirm "Create database $DB_NAME on instance $RDB_INSTANCE_ID?"; then
    scw rdb database create instance-id="$RDB_INSTANCE_ID" name="$DB_NAME" region="$REGION" >/dev/null
    ok "database $DB_NAME created"
  else
    warn "database creation skipped"
  fi
fi

ok "bootstrap done"
