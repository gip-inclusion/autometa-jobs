#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Trigger N chunks of T1 extraction in parallel against a chosen pipeline.

Loads the manifest written by `di_t1_prep.py`, then POSTs one run per chunk.
All triggered at once (effective concurrency = number of chunks). Use the
`--chunks-from` and `--chunks-to` args to launch a subset.

Usage:
  source .env.local first.
  uv run scripts/di_t1_launch.py <pipeline_id> [--manifest data/di/t1_manifest_*.json] [--chunks-from 1] [--chunks-to 4]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pipeline_id")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--chunks-from", type=int, default=1)
    ap.add_argument("--chunks-to", type=int, default=None)
    args = ap.parse_args()

    base = os.environ.get("PIPOMETA_URL")
    api_key = os.environ.get("PIPOMETA_API_KEY")
    if not (base and api_key):
        print("ERR: source .env.local + export PIPOMETA_API_KEY first", file=sys.stderr)
        return 1

    if args.manifest:
        manifest_path = Path(args.manifest)
    else:
        candidates = sorted(DATA.glob("t1_manifest_*.json"))
        if not candidates:
            print("ERR: no t1_manifest_*.json found", file=sys.stderr)
            return 1
        manifest_path = candidates[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunks = manifest["chunks"]

    lo = args.chunks_from - 1
    hi = args.chunks_to if args.chunks_to is not None else len(chunks)
    chunks = chunks[lo:hi]

    print(f"Triggering {len(chunks)} run(s) on pipeline {args.pipeline_id}")
    runs = []
    with httpx.Client(base_url=base.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"}, timeout=30) as c:
        for chunk in chunks:
            r = c.post(
                f"/pipelines/{args.pipeline_id}/runs",
                json={"input_uri": chunk["run_uri"]},
            )
            r.raise_for_status()
            d = r.json()
            print(f"  chunk {chunk['chunk_id']:>2}: run {d['id']} status={d['status']} job_run={d.get('scaleway_job_run_id')}")
            runs.append({"chunk_id": chunk["chunk_id"], "run_id": d["id"], "status": d["status"]})

    out_path = DATA / f"t1_runs_{manifest['batch_id']}_{args.pipeline_id[:8]}.json"
    out_path.write_text(json.dumps({"pipeline_id": args.pipeline_id, "runs": runs}, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
