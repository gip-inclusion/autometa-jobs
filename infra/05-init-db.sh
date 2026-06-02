#!/usr/bin/env bash
# Apply pending Alembic migrations to the pipometa database.
# Requires PIPOMETA_DATABASE_URL (asyncpg-style URL is fine; alembic env.py rewrites
# the driver to psycopg sync internally).

source "$(dirname "$0")/lib.sh"

URL="${PIPOMETA_DATABASE_URL:?PIPOMETA_DATABASE_URL must be set}"
ROOT="$(dirname "$0")/../orchestrator"

[[ -f "$ROOT/alembic.ini" ]] || die "missing $ROOT/alembic.ini"

cd "$ROOT"
info "running alembic upgrade head"
alembic upgrade head
ok "schema at head"
