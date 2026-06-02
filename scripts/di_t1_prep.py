#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "boto3>=1.35"]
# ///
"""Build the T1 (solutions structurées) input set:

  1. Join services with structures, keep services whose structure matches a T1
     bucket (reseaux_porteurs in any T1 code OR nom matches a T1 keyword).
  2. Write the full T1 set as JSONL.
  3. Split into N chunks of ~equal size for parallel processing.
  4. Upload each chunk to S3 + a per-chunk run.json with the extraction prompt.

Output (S3):
  - inputs/di/t1/<batch_id>/services_full.jsonl              (~5,621 services)
  - inputs/di/t1/<batch_id>/chunk_NN/services.jsonl          (~chunk_size services)
  - inputs/di/t1/<batch_id>/chunk_NN/run.json                (prompt with chunk URIs)

Usage: uv run scripts/di_t1_prep.py [--chunks N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import boto3
import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
STRUCT = DATA / "structures.parquet"
SERV = DATA / "services.parquet"
T1_LOCAL = DATA / "t1_services.jsonl"

T1_CODES = [
    "aci", "ei", "etti", "ea", "esat", "geiq", "plie",
    "mission-locale", "cap-emploi-reseau-cheops",
    "afpa", "adie", "cidff", "residences-fjt", "spip",
]

T1_NAME_KEYWORDS = [
    r"\bEATT\b",
    r"\bEPIDE\b",
    r"\bE2C\b",
    r"[ée]cole\s+de\s+la\s+(?:deuxi|2)[èe]me\s+chance",
    r"\bAPEC\b",
    r"apprentis\W{1,3}auteuil",
    r"mission\s+locale",
    r"cap\s+emploi",
]

KEEP_FIELDS = [
    "id", "source", "score_qualite", "nom", "type", "thematiques",
    "publics", "publics_precisions", "conditions_acces", "description",
    "structure_id",
]


def build_t1_clause() -> str:
    parts = []
    codes_quoted = ", ".join(f"'{c}'" for c in T1_CODES)
    parts.append(
        f"(st.reseaux_porteurs IS NOT NULL AND list_has_any(st.reseaux_porteurs, [{codes_quoted}]))"
    )
    for p in T1_NAME_KEYWORDS:
        p_safe = p.replace("'", "''")
        parts.append(
            f"regexp_matches(COALESCE(st.nom, '') || ' ' || COALESCE(st.description, ''), '(?i){p_safe}')"
        )
    return "(" + " OR ".join(parts) + ")"


def jsonl_serialize(rec: dict) -> str:
    out = {}
    for k, v in rec.items():
        if hasattr(v, "tolist"):
            v = v.tolist()
        out[k] = v
    return json.dumps(out, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=4, help="number of chunks to split T1 into")
    ap.add_argument("--batch-id", default=None, help="S3 batch id (default: today)")
    args = ap.parse_args()

    bucket = os.environ.get("PIPOMETA_BUCKET")
    endpoint = os.environ.get("PIPOMETA_S3_ENDPOINT", "https://s3.fr-par.scw.cloud")
    if not (bucket and os.environ.get("AWS_ACCESS_KEY_ID")):
        print("ERR: source .env.local + scw config first", file=sys.stderr)
        return 1
    batch_id = args.batch_id or f"t1-{date.today().isoformat()}"

    if not STRUCT.exists() or not SERV.exists():
        print("ERR: missing parquets.", file=sys.stderr)
        return 1

    con = duckdb.connect()
    print("Joining services × structures with T1 filter...")
    cols = ", ".join(f"s.{c}" for c in KEEP_FIELDS)
    sql = f"""
        SELECT {cols}
        FROM read_parquet('{SERV}') s
        JOIN read_parquet('{STRUCT}') st ON st.id = s.structure_id
        WHERE {build_t1_clause()}
        ORDER BY s.id
    """
    rel = con.execute(sql)
    cols_meta = [c[0] for c in rel.description]
    rows = rel.fetchall()
    print(f"T1 services: {len(rows):,}")

    T1_LOCAL.write_text(
        "\n".join(jsonl_serialize(dict(zip(cols_meta, r))) for r in rows) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {T1_LOCAL} ({T1_LOCAL.stat().st_size:,} bytes)")

    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="fr-par")

    full_key = f"inputs/di/{batch_id}/services_full.jsonl"
    s3.upload_file(str(T1_LOCAL), bucket, full_key)
    print(f"Uploaded full set → s3://{bucket}/{full_key}")

    schema_key = "inputs/di/2026-05-04/schema.v0.2.json"  # already on S3 from earlier

    # Chunking
    chunks = args.chunks
    chunk_size = (len(rows) + chunks - 1) // chunks
    chunk_uris: list[str] = []
    chunk_run_uris: list[str] = []
    for i in range(chunks):
        lo = i * chunk_size
        hi = min(lo + chunk_size, len(rows))
        chunk_rows = rows[lo:hi]
        if not chunk_rows:
            break
        chunk_jsonl = "\n".join(
            jsonl_serialize(dict(zip(cols_meta, r))) for r in chunk_rows
        ) + "\n"
        chunk_label = f"chunk_{i+1:02d}"
        services_key = f"inputs/di/{batch_id}/{chunk_label}/services.jsonl"
        run_key = f"inputs/di/{batch_id}/{chunk_label}/run.json"
        s3.put_object(Bucket=bucket, Key=services_key, Body=chunk_jsonl.encode("utf-8"))
        services_uri = f"s3://{bucket}/{services_key}"
        run_uri = f"s3://{bucket}/{run_key}"

        prompt = f"""Tu vas extraire des champs structurés depuis {len(chunk_rows)} services d'insertion (chunk {i+1}/{chunks} du Tier-1 « solutions structurées »), selon meta-di v0.2.

INPUTS
- Schéma cible : `s3://{bucket}/{schema_key}`. Lis-le. Le bloc `$llm_arbitration_required` détaille les sept ambiguïtés à arbitrer contextuellement.
- Données : `{services_uri}` (JSONL UTF-8, ~{len(chunk_jsonl):,} bytes, {len(chunk_rows)} services).

Téléchargement :
```bash
python3 - <<'PY'
import boto3, os
s3 = boto3.client("s3", endpoint_url="{endpoint}", region_name="fr-par")
s3.download_file("{bucket}", "{schema_key}", "/tmp/schema.json")
s3.download_file("{bucket}", "{services_key}", "/tmp/services.jsonl")
PY
```

OUTPUT — clé S3 unique attendue :
```python
import os, boto3
from datetime import datetime, timezone
bucket = os.environ['PIPOMETA_OUTPUT_BUCKET']
run_id = os.environ['PIPOMETA_RUN_ID']
ts = datetime.now(timezone.utc).strftime('%Y/%m/%d')
out_key = f'runs/{{ts}}/<PIPELINE_NAME>/{{run_id}}/extracted.jsonl'
# Remplace <PIPELINE_NAME> par la valeur de $PIPOMETA_PIPELINE_NAME (di-extract-strict-* ou di-extract).
```

CHECKPOINT : tous les 10 batches, refaire l'upload (le dernier écrase).

CONSIGNES
Procède par batchs de 25-30 services par tour. Suis ton system prompt — INTERDICTION de scripts de règles/regex pour l'extraction. Le contenu des records doit venir de TON raisonnement par-service.

Message final (capté comme `output.md`) : résumé court markdown — services traités, URI S3 produite, distribution principale, top 10 conflits, services douteux à relire.
"""
        s3.put_object(
            Bucket=bucket,
            Key=run_key,
            Body=json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        chunk_uris.append(services_uri)
        chunk_run_uris.append(run_uri)
        print(f"  {chunk_label}: {len(chunk_rows)} services → {run_uri}")

    # Manifest
    manifest = {
        "batch_id": batch_id,
        "total_services": len(rows),
        "chunks": [
            {"chunk_id": i + 1, "services_uri": s_uri, "run_uri": r_uri}
            for i, (s_uri, r_uri) in enumerate(zip(chunk_uris, chunk_run_uris))
        ],
        "schema_uri": f"s3://{bucket}/{schema_key}",
    }
    manifest_key = f"inputs/di/{batch_id}/manifest.json"
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    local_manifest = DATA / f"t1_manifest_{batch_id}.json"
    local_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote manifest: {local_manifest}")
    print(f"  S3 manifest: s3://{bucket}/{manifest_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
