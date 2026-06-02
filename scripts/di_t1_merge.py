#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35", "tabulate>=0.9"]
# ///
"""Merge the extracted.jsonl from 4 T1 chunk runs into a single file.

Reads `data/di/t1_runs_*.json` (run manifest), downloads each run's
`extracted.jsonl` from S3, concatenates into `data/di/t1_extracted.jsonl`,
and prints a summary.

Usage: uv run scripts/di_t1_merge.py [--manifest data/di/t1_runs_<id>.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import boto3
from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--pipeline-name", default="di-extract-strict")
    args = ap.parse_args()

    if args.manifest:
        manifest_path = Path(args.manifest)
    else:
        candidates = sorted(DATA.glob("t1_runs_*.json"))
        if not candidates:
            print("ERR: no t1_runs_*.json", file=sys.stderr)
            return 1
        manifest_path = candidates[-1]
    runs = json.loads(manifest_path.read_text(encoding="utf-8"))["runs"]

    bucket = os.environ.get("PIPOMETA_BUCKET", "pipometa")
    endpoint = os.environ.get("PIPOMETA_S3_ENDPOINT", "https://s3.fr-par.scw.cloud")
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="fr-par")

    rows = []
    merged_local = DATA / "t1_extracted.jsonl"
    total_records = 0
    found_paths = []
    with merged_local.open("w", encoding="utf-8") as out_f:
        for run in runs:
            rid = run["run_id"]
            chunk_id = run["chunk_id"]
            local = DATA / "runs" / rid / "extracted.jsonl"
            local.parent.mkdir(parents=True, exist_ok=True)

            # Try multiple S3 paths (date variations + pipeline-name variations)
            tried = []
            for date_str in ["2026/05/04", "2026/05/05"]:
                for pipeline in [args.pipeline_name, "di-extract-strict"]:
                    key = f"runs/{date_str}/{pipeline}/{rid}/extracted.jsonl"
                    tried.append(key)
                    try:
                        s3.head_object(Bucket=bucket, Key=key)
                        s3.download_file(bucket, key, str(local))
                        found_paths.append(key)
                        break
                    except Exception:
                        continue
                else:
                    continue
                break
            else:
                rows.append([f"chunk {chunk_id}", rid[:8], "MISSING", 0])
                continue

            n = 0
            with local.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    out_f.write(line + "\n")
                    n += 1
            total_records += n
            rows.append([f"chunk {chunk_id}", rid[:8], "ok", n])

    rows.append(["TOTAL", "", "", total_records])
    print(tabulate(rows, headers=["chunk", "run id", "status", "records"], tablefmt="github"))
    print()
    print(f"Wrote {merged_local} ({merged_local.stat().st_size:,} bytes, {total_records} records)")
    if found_paths:
        print()
        print("S3 keys downloaded:")
        for p in found_paths:
            print(f"  s3://{bucket}/{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
