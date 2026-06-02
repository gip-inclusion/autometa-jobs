#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "boto3>=1.35"]
# ///
"""Prepare and upload inputs for a di-extract run.

Steps:
  1. Read data/di/sample_1500.parquet
  2. Project to relevant fields → JSONL
  3. Upload JSONL to s3://<bucket>/inputs/di/<batch_id>/sample_1500.jsonl
  4. Upload pipelines/meta-di-schema.v0.2.json to s3://<bucket>/inputs/di/<batch_id>/schema.json
  5. Build run.json (the prompt the worker hands to the agent) and upload it
  6. Print the run_uri to use when triggering the run

Env required (from .env.local + scw config):
  PIPOMETA_BUCKET
  PIPOMETA_S3_ENDPOINT
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

Usage: uv run scripts/di_extract_launch.py [batch_id]
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
SOURCE = DATA / "sample_1500.parquet"
SCHEMA = ROOT / "pipelines" / "meta-di-schema.v0.2.json"

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
        print("ERR: AWS_ACCESS_KEY_ID/SECRET unset.", file=sys.stderr)
        return 1
    if not SOURCE.exists():
        print(f"ERR: {SOURCE} missing. Run scripts/di_prep.py first.", file=sys.stderr)
        return 1
    if not SCHEMA.exists():
        print(f"ERR: {SCHEMA} missing.", file=sys.stderr)
        return 1

    services_key = f"inputs/di/{batch_id}/sample_1500.jsonl"
    schema_key = f"inputs/di/{batch_id}/schema.v0.2.json"
    run_key = f"inputs/di/{batch_id}/extract_run.json"
    services_uri = f"s3://{bucket}/{services_key}"
    schema_uri = f"s3://{bucket}/{schema_key}"
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

    print(f"Uploading schema → {schema_uri}")
    s3.put_object(
        Bucket=bucket,
        Key=schema_key,
        Body=SCHEMA.read_bytes(),
        ContentType="application/json; charset=utf-8",
    )

    prompt = f"""Tu vas extraire des champs structurés depuis 1 500 services d'insertion, selon la surcouche meta-di v0.2.

INPUTS

- Schéma cible (`schema_uri`) : `{schema_uri}`. C'EST TON CONTRAT DE SORTIE. Lis-le intégralement avant de commencer. Le bloc `$llm_arbitration_required` détaille les sept ambiguïtés où tu dois trancher contextuellement.

- Données (`batch_uri`) : `{services_uri}` (JSONL UTF-8, ~600 Ko).

Pour télécharger les deux :

```bash
python3 - <<'PY'
import boto3, os
s3 = boto3.client("s3", endpoint_url="{endpoint}", region_name="fr-par")
s3.download_file("{bucket}", "{schema_key}", "/tmp/schema.json")
s3.download_file("{bucket}", "{services_key}", "/tmp/services.jsonl")
print("schema:", os.path.getsize("/tmp/schema.json"), "bytes")
print("services:", os.path.getsize("/tmp/services.jsonl"), "bytes")
PY
```

OUTPUT

Tu n'écris PAS la sortie en chat (1 500 records JSON ne tiennent pas). Tu uploades directement vers S3, à la clé attendue par l'orchestrateur :

```python
import os, boto3
from datetime import datetime, timezone
bucket = os.environ['PIPOMETA_OUTPUT_BUCKET']
run_id = os.environ['PIPOMETA_RUN_ID']
ts = datetime.now(timezone.utc).strftime('%Y/%m/%d')
key = f'runs/{{ts}}/di-extract/{{run_id}}/extracted.jsonl'
s3 = boto3.client("s3", endpoint_url="{endpoint}", region_name="fr-par")
s3.upload_file('/tmp/extracted.jsonl', bucket, key)
print('uploaded:', f's3://{{bucket}}/{{key}}')
```

DANS TON MESSAGE FINAL : un résumé court (markdown). Combien de services traités, l'URI S3 du JSONL produit, comptages des principaux champs remplis, top 10 des conflits, échantillons douteux à relire humainement. C'est ça que l'orchestrateur capture comme `output.md`.

CONSIGNES

Suis les sept règles d'arbitrage du `$llm_arbitration_required` du schéma — elles sont aussi dans ton system prompt. Anti-hallucination : si le texte ne le dit pas, le champ est `null` (scalaires) ou `[]` (listes). Chaque scalaire est un objet `{{value, source_field, evidence_substring}}`, pas une valeur nue.

Procède par batchs (30–50 services par tour, écriture incrémentale dans `/tmp/extracted.jsonl`). Upload final unique à la fin.
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
    print(f"  schema_uri   = {schema_uri}")
    print(f"  run_uri      = {run_uri}")
    print()
    print("Trigger run with:")
    print(f"  curl -X POST $PIPOMETA_URL/pipelines/<DI_EXTRACT_PIPELINE_ID>/runs \\")
    print(f"    -H 'Authorization: Bearer $PIPOMETA_API_KEY' \\")
    print(f"    -H 'Content-Type: application/json' \\")
    print(f"    -d '{{\"input_uri\": \"{run_uri}\"}}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
