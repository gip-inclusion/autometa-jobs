#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["asyncpg>=0.30"]
# ///
"""One-shot recovery: re-sync the orchestrator container's secret env vars
with the values currently stored in Secret Manager / scw config.

The orchestrator's PIPOMETA_API_KEY had drifted from the latest Secret Manager
revision, so curl auth was returning 401. We can't read the current container
secrets (Scaleway hashes them in API responses), so we rebuild ALL four secret
env vars from authoritative sources and push them in one update call:

  - PIPOMETA_API_KEY            <- Secret Manager (latest rev of $PIPOMETA_SECRET_API_KEY_ID)
  - PIPOMETA_DATABASE_URL       <- assembled from .env.local + DB password Secret Manager
  - PIPOMETA_SCALEWAY_SECRET_KEY <- scw config get secret-key
  - PIPOMETA_CRON_SECRET        <- read from existing container plain env vars

Verifies the rebuilt DATABASE_URL by connecting to Postgres before pushing.

Usage: source .env.local first, then `uv run scripts/_resync_orchestrator_secrets.py`.

This script is named with a leading underscore to mark it as a one-time
operational tool, not a routine workflow.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from urllib.parse import quote


def fail(msg: str) -> None:
    print(f"ERR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], capture: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if r.returncode != 0:
        fail(f"command failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def fetch_secret(secret_id: str, region: str) -> str:
    raw = run([
        "scw", "secret", "version", "access", secret_id,
        "revision=latest", f"region={region}", "-o", "json",
    ])
    obj = json.loads(raw)
    return base64.b64decode(obj["data"]).decode().strip()


async def test_db(url: str) -> None:
    import asyncpg
    # asyncpg doesn't accept the SQLAlchemy `postgresql+asyncpg://` prefix.
    conn_url = url.replace("postgresql+asyncpg://", "postgres://", 1)
    # asyncpg expects `ssl=require` differently; strip the query param and
    # pass via kwargs.
    if "?ssl=require" in conn_url:
        conn_url = conn_url.replace("?ssl=require", "")
        ssl = "require"
    else:
        ssl = None
    conn = await asyncpg.connect(conn_url, ssl=ssl)
    try:
        v = await conn.fetchval("SELECT 1")
        if v != 1:
            fail(f"unexpected SELECT 1 result: {v!r}")
    finally:
        await conn.close()


def main() -> int:
    region = os.environ.get("PIPOMETA_REGION") or fail("PIPOMETA_REGION unset (source .env.local)")
    container_id = os.environ.get("PIPOMETA_CONTAINER_ID") or fail("PIPOMETA_CONTAINER_ID unset")
    api_key_secret_id = os.environ.get("PIPOMETA_SECRET_API_KEY_ID") or fail("PIPOMETA_SECRET_API_KEY_ID unset")
    db_password_secret_id = os.environ.get("PIPOMETA_SECRET_DB_PASSWORD_ID") or fail("PIPOMETA_SECRET_DB_PASSWORD_ID unset")
    db_user = os.environ.get("PIPOMETA_DB_USER") or fail("PIPOMETA_DB_USER unset")
    db_host = os.environ.get("PIPOMETA_DB_HOST") or fail("PIPOMETA_DB_HOST unset")
    db_port = os.environ.get("PIPOMETA_DB_PORT") or fail("PIPOMETA_DB_PORT unset")
    db_name = os.environ.get("PIPOMETA_DB_NAME") or fail("PIPOMETA_DB_NAME unset")

    # 1. Read the existing container's plain env vars (to recover CRON_SECRET).
    print("Fetching current container config...")
    container_raw = run([
        "scw", "container", "container", "get", container_id,
        f"region={region}", "-o", "json",
    ])
    container = json.loads(container_raw)
    plain_env = container.get("environment_variables") or {}
    cron_secret = plain_env.get("PIPOMETA_CRON_SECRET") or fail("PIPOMETA_CRON_SECRET not in container plain env")
    existing_secret_keys = sorted([s["key"] for s in container.get("secret_environment_variables", [])])
    print(f"  existing secret keys: {existing_secret_keys}")

    # 2. Fetch fresh secret values.
    print("Fetching API key (Secret Manager, latest)...")
    api_key = fetch_secret(api_key_secret_id, region)
    print(f"  api_key: {len(api_key)} chars, prefix={api_key[:4]}...")

    print("Fetching DB password (Secret Manager, latest)...")
    db_password = fetch_secret(db_password_secret_id, region)
    print(f"  db_password: {len(db_password)} chars")

    print("Fetching scaleway secret key (scw config)...")
    scw_secret = run(["scw", "config", "get", "secret-key"]).strip()
    if not scw_secret:
        fail("scw config has no secret-key")
    print(f"  scw_secret: {len(scw_secret)} chars")

    # 3. Assemble DATABASE_URL.
    db_url = (
        f"postgresql+asyncpg://{db_user}:{quote(db_password, safe='')}"
        f"@{db_host}:{db_port}/{db_name}?ssl=require"
    )
    print(f"  database_url: postgresql+asyncpg://{db_user}:***@{db_host}:{db_port}/{db_name}?ssl=require")

    # 4. Verify DB URL works by connecting.
    print("Verifying DB connectivity with assembled URL...")
    try:
        asyncio.run(test_db(db_url))
        print("  OK (SELECT 1 succeeded)")
    except Exception as e:
        fail(f"DB connection failed: {e}")

    # 5. Push update with ALL 4 secret env vars in one shot.
    secrets = [
        ("PIPOMETA_API_KEY", api_key),
        ("PIPOMETA_DATABASE_URL", db_url),
        ("PIPOMETA_SCALEWAY_SECRET_KEY", scw_secret),
        ("PIPOMETA_CRON_SECRET", cron_secret),
    ]
    args = [
        "scw", "container", "container", "update", container_id,
        f"region={region}",
    ]
    for i, (k, v) in enumerate(secrets):
        args.append(f"secret-environment-variables.{i}.key={k}")
        args.append(f"secret-environment-variables.{i}.value={v}")

    print(f"Pushing update (4 secret env vars: {[k for k,_ in secrets]})...")
    redacted_args = [a if "value=" not in a else a.split("value=")[0] + "value=<redacted>" for a in args]
    print("  cmd:", " ".join(redacted_args))
    out = run(args)
    res = json.loads(out)
    print(f"  status: {res.get('status')}, updated_at: {res.get('updated_at')}")
    print()
    print("Container updated. Allow ~30-60s for redeploy. Then re-test auth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
