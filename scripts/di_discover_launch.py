#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "boto3>=1.35"]
# ///
"""Prepare and upload inputs for a di-discover run.

Steps:
  1. Read data/di/discovery_300.parquet
  2. Project to relevant fields → JSONL (in-memory)
  3. Upload JSONL to s3://<bucket>/inputs/di/<batch_id>/services.jsonl
  4. Build run.json (the prompt that the worker hands to the agent)
  5. Upload run.json to s3://<bucket>/inputs/di/<batch_id>/run.json
  6. Print the input_uri to use when triggering the run

Env required (sourced from .env.local + Secret Manager):
  PIPOMETA_BUCKET
  PIPOMETA_S3_ENDPOINT
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY  (Scaleway-compatible)

Usage: uv run scripts/di_discover_launch.py [batch_id]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import boto3
import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
SOURCE = DATA / "discovery_300.parquet"

KEEP_FIELDS = [
    "id",
    "source",
    "score_qualite",
    "nom",
    "type",
    "thematiques",
    "publics",
    "publics_precisions",
    "conditions_acces",
    "description",
]


def jsonl_from_parquet(path: Path) -> bytes:
    con = duckdb.connect()
    cols = ", ".join(KEEP_FIELDS)
    rel = con.execute(f"SELECT {cols} FROM read_parquet('{path}')")
    cols_meta = [c[0] for c in rel.description]
    out = []
    for row in rel.fetchall():
        d = {}
        for k, v in zip(cols_meta, row):
            if hasattr(v, "tolist"):
                v = v.tolist()
            d[k] = v
        out.append(json.dumps(d, ensure_ascii=False))
    return ("\n".join(out) + "\n").encode("utf-8")


def main() -> int:
    batch_id = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

    bucket = os.environ.get("PIPOMETA_BUCKET")
    endpoint = os.environ.get("PIPOMETA_S3_ENDPOINT", "https://s3.fr-par.scw.cloud")
    if not bucket:
        print("ERR: PIPOMETA_BUCKET unset. Source .env.local first.", file=sys.stderr)
        return 1
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print("ERR: AWS_ACCESS_KEY_ID/SECRET unset. Source .env.local first.", file=sys.stderr)
        return 1
    if not SOURCE.exists():
        print(f"ERR: {SOURCE} missing. Run scripts/di_prep.py first.", file=sys.stderr)
        return 1

    services_key = f"inputs/di/{batch_id}/services.jsonl"
    run_key = f"inputs/di/{batch_id}/run.json"
    services_uri = f"s3://{bucket}/{services_key}"
    run_uri = f"s3://{bucket}/{run_key}"

    body = jsonl_from_parquet(SOURCE)
    n_lines = body.count(b"\n")
    print(f"JSONL prepared: {len(body):,} bytes ({n_lines} services)")

    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="fr-par")

    print(f"Uploading services JSONL → {services_uri}")
    s3.put_object(
        Bucket=bucket,
        Key=services_key,
        Body=body,
        ContentType="application/x-ndjson; charset=utf-8",
    )

    prompt = f"""Tu vas découvrir un schéma enrichi à partir d'un échantillon de 300 services d'insertion.

L'échantillon est sur S3 : `{services_uri}` (JSONL UTF-8).

Pour le télécharger, lance ceci via Bash (boto3 et les credentials AWS sont disponibles dans l'environnement) :

```bash
python3 - <<'PY'
import boto3, os
s3 = boto3.client("s3", endpoint_url="{endpoint}", region_name="fr-par")
s3.download_file("{bucket}", "{services_key}", "/tmp/services.jsonl")
import os; print("size:", os.path.getsize("/tmp/services.jsonl"), "bytes")
PY
```

Puis explore-le avec Python (lire ligne à ligne avec `json`, faire des comptages, des regex, etc.). Tu peux installer des paquets si besoin avec `pip install --user ...`.

Identifie les patterns décrits dans le system prompt et produis ton message final selon le format demandé (`proposed_schema`, `value_tally`, `discovery_notes`).

Le `discovered_from_batch` à mettre dans `proposed_schema` est : `{services_uri}`.
"""

    run_body = json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2).encode("utf-8")
    print(f"Uploading run.json → {run_uri}")
    s3.put_object(
        Bucket=bucket,
        Key=run_key,
        Body=run_body,
        ContentType="application/json; charset=utf-8",
    )

    print()
    print("Inputs ready.")
    print(f"  services_uri = {services_uri}")
    print(f"  run_uri      = {run_uri}")
    print()
    print("Next: trigger the run with:")
    print(f"  curl -X POST $PIPOMETA_URL/pipelines/<PIPELINE_ID>/runs \\")
    print(f"    -H 'Authorization: Bearer $PIPOMETA_API_KEY' \\")
    print(f"    -H 'Content-Type: application/json' \\")
    print(f"    -d '{{\"input_uri\": \"{run_uri}\"}}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
