#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "httpx>=0.27", "numpy>=1.26", "pyarrow>=15"]
# ///
"""Download the data·inclusion services parquet and produce stratified samples.

Reads `data/di/services.parquet` (downloads it if absent), then writes:
  - data/di/sample_1500.parquet  (validation set for di-extract)
  - data/di/discovery_300.parquet (strict subset, for di-discover)
  - data/di/manifest.json (parameters + per-stratum counts + source/score breakdown)

Sampling:
  - 3 strata by free-text richness (rich / norm-only / sparse)
  - within each stratum, weighted-without-replacement sampling
    weight = (1 / source_count^ALPHA) * score_boost(score_qualite)
  - SEED is fixed; rerunning produces the same sample
  - the 300 set is a strict subset of the 1500 set

Usage: uv run scripts/di_prep.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import httpx
import numpy as np
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
PARQUET = DATA / "services.parquet"
SAMPLE_VALIDATION = DATA / "sample_1500.parquet"
SAMPLE_DISCOVERY = DATA / "discovery_300.parquet"
MANIFEST = DATA / "manifest.json"
DATASET_API = "https://www.data.gouv.fr/api/1/datasets/6233723c2c1e4a54af2f6b2d/"

SEED = 20260504
N_VALIDATION = 1500
N_DISCOVERY = 300
ALPHA = 0.5  # source count exponent for weight

QUOTAS = {
    "rich": 0.65,
    "norm_only": 0.20,
    "sparse": 0.15,
}


def find_services_parquet_url() -> tuple[str, int]:
    r = httpx.get(DATASET_API, timeout=30.0)
    r.raise_for_status()
    payload = r.json()
    candidates = [
        res for res in payload.get("resources", [])
        if res.get("format") == "parquet"
        and "services" in (res.get("title") or "").lower()
    ]
    if not candidates:
        raise RuntimeError("No parquet resource for `services` found in dataset payload")
    candidates.sort(key=lambda res: res.get("last_modified") or "", reverse=True)
    chosen = candidates[0]
    return chosen["url"], int(chosen.get("filesize") or 0)


def download(url: str, dest: Path, expected_bytes: int | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {url}\n  -> {dest}")
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", expected_bytes or 0)) or None
        written = 0
        last_pct = -1
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = int(written * 100 / total)
                    if pct != last_pct and pct % 5 == 0:
                        print(f"  {pct}% ({written:,}/{total:,} bytes)", flush=True)
                        last_pct = pct
    tmp.replace(dest)
    print(f"  done: {dest.stat().st_size:,} bytes")


def ensure_parquet() -> None:
    if PARQUET.exists():
        print(f"Parquet present: {PARQUET} ({PARQUET.stat().st_size:,} bytes)")
        return
    url, size = find_services_parquet_url()
    download(url, PARQUET, expected_bytes=size)


def score_boost_sql() -> str:
    return (
        "CASE "
        "WHEN score_qualite < 0.50 THEN 2.0 "
        "WHEN score_qualite < 0.75 THEN 1.5 "
        "ELSE 1.0 END"
    )


def stratum_sql() -> str:
    rich = (
        "(publics_precisions IS NOT NULL AND length(trim(publics_precisions)) > 0) "
        "OR (conditions_acces IS NOT NULL AND length(trim(conditions_acces)) > 0)"
    )
    has_publics = "(publics IS NOT NULL AND len(publics) > 0)"
    return (
        "CASE "
        f"WHEN {rich} THEN 'rich' "
        f"WHEN {has_publics} THEN 'norm_only' "
        "ELSE 'sparse' END"
    )


def weighted_sample_indices(
    weights: np.ndarray, k: int, rng: np.random.Generator
) -> np.ndarray:
    """Weighted sampling without replacement via Efraimidis-Spirakis.

    Each item gets key = -log(uniform(0,1)) / weight; take the k items with
    the smallest keys. Equivalent to numpy's choice(replace=False, p=...) but
    handles k > positive-weight count by falling back to all indices, and is
    deterministic given the rng.
    """
    n = len(weights)
    if k >= n:
        return np.arange(n)
    u = rng.random(n)
    # avoid log(0); weights are guaranteed > 0 by our caller
    keys = -np.log(u) / weights
    return np.argpartition(keys, k)[:k]


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    ensure_parquet()

    con = duckdb.connect()

    # Annotate every row with stratum + weight components
    sql = f"""
        WITH base AS (
            SELECT
                id,
                source,
                score_qualite,
                {stratum_sql()} AS stratum
            FROM read_parquet('{PARQUET}')
        ),
        sc AS (
            SELECT source, COUNT(*) AS source_count FROM base GROUP BY source
        )
        SELECT
            base.id,
            base.source,
            base.score_qualite,
            base.stratum,
            sc.source_count,
            POWER(sc.source_count, {ALPHA}) AS source_norm,
            ({score_boost_sql()}) AS score_boost,
            (({score_boost_sql()}) / POWER(sc.source_count, {ALPHA})) AS weight
        FROM base
        JOIN sc ON sc.source = base.source
    """
    rows = con.execute(sql).fetchnumpy()
    ids = rows["id"]
    sources = rows["source"]
    scores = rows["score_qualite"]
    strata = rows["stratum"]
    weights = rows["weight"].astype(np.float64)

    n_total = len(ids)
    print(f"\nCorpus: {n_total:,} rows")
    for stratum_name in ("rich", "norm_only", "sparse"):
        n = int(np.sum(strata == stratum_name))
        print(f"  stratum {stratum_name}: {n:,} ({n/n_total:.1%})")

    rng = np.random.default_rng(SEED)

    # Phase A: sample N_VALIDATION rows (stratified, weighted)
    selected_validation: list[int] = []
    per_stratum_picks: dict[str, np.ndarray] = {}
    for stratum_name, share in QUOTAS.items():
        target = int(round(N_VALIDATION * share))
        idx_pool = np.where(strata == stratum_name)[0]
        w = weights[idx_pool]
        if len(idx_pool) == 0:
            print(f"WARN: stratum {stratum_name} is empty")
            per_stratum_picks[stratum_name] = np.array([], dtype=int)
            continue
        if target > len(idx_pool):
            print(
                f"WARN: stratum {stratum_name} has only {len(idx_pool)} rows, "
                f"target was {target}"
            )
            target = len(idx_pool)
        local_picks = weighted_sample_indices(w, target, rng)
        global_picks = idx_pool[local_picks]
        per_stratum_picks[stratum_name] = global_picks
        selected_validation.extend(global_picks.tolist())

    selected_validation_arr = np.array(sorted(selected_validation))
    print(f"\nValidation set: {len(selected_validation_arr):,} rows")

    # Phase B: pick discovery_300 as a strict subset of the validation set,
    # preserving stratum proportions. Within each stratum, take the first
    # N_DISCOVERY * share rows (the rng order from phase A is deterministic).
    selected_discovery: list[int] = []
    for stratum_name, share in QUOTAS.items():
        target = int(round(N_DISCOVERY * share))
        picks = per_stratum_picks[stratum_name]
        if len(picks) == 0:
            continue
        target = min(target, len(picks))
        # picks is already in the order returned by weighted_sample_indices,
        # which is deterministic given SEED. Take first `target`.
        selected_discovery.extend(picks[:target].tolist())

    selected_discovery_arr = np.array(sorted(selected_discovery))
    print(f"Discovery set: {len(selected_discovery_arr):,} rows")
    assert set(selected_discovery_arr).issubset(set(selected_validation_arr)), (
        "discovery is not a subset of validation"
    )

    # Materialize the parquets by joining selected ids back to the full corpus
    def write_subset(target_ids: np.ndarray, out: Path) -> None:
        ids_table = pa.table({"id": pa.array(target_ids)})
        con.register("selected_ids", ids_table)
        try:
            con.execute(
                f"""
                COPY (
                    SELECT s.*
                    FROM read_parquet('{PARQUET}') s
                    JOIN selected_ids ON selected_ids.id = s.id
                ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )
        finally:
            con.unregister("selected_ids")
        print(f"Wrote {out} ({out.stat().st_size:,} bytes)")

    validation_ids = ids[selected_validation_arr]
    discovery_ids = ids[selected_discovery_arr]
    write_subset(validation_ids, SAMPLE_VALIDATION)
    write_subset(discovery_ids, SAMPLE_DISCOVERY)

    # Manifest
    def stratum_breakdown(picks: np.ndarray) -> dict:
        d: dict[str, dict] = {}
        for stratum_name in ("rich", "norm_only", "sparse"):
            mask = strata[picks] == stratum_name
            sub = picks[mask]
            sub_sources = sources[sub]
            sub_scores = scores[sub]
            unique, counts = np.unique(sub_sources, return_counts=True)
            source_dist = {
                str(s): int(c) for s, c in sorted(
                    zip(unique, counts), key=lambda x: -x[1]
                )
            }
            d[stratum_name] = {
                "n": int(len(sub)),
                "score_p50": float(np.median(sub_scores)) if len(sub) else None,
                "score_mean": float(np.mean(sub_scores)) if len(sub) else None,
                "by_source": source_dist,
            }
        return d

    manifest = {
        "seed": SEED,
        "alpha": ALPHA,
        "score_boost": {"<0.50": 2.0, "0.50-0.75": 1.5, ">=0.75": 1.0},
        "richness_def": "publics_precisions OR conditions_acces non-empty",
        "stratum_def": {
            "rich": "publics_precisions OR conditions_acces non-empty",
            "norm_only": "no free text, but publics non-empty",
            "sparse": "no free text, no publics",
        },
        "quotas": QUOTAS,
        "corpus_total": int(n_total),
        "corpus_strata": {
            s: int(np.sum(strata == s)) for s in ("rich", "norm_only", "sparse")
        },
        "validation": {
            "n": int(len(selected_validation_arr)),
            "path": str(SAMPLE_VALIDATION.relative_to(ROOT)),
            "by_stratum": stratum_breakdown(selected_validation_arr),
        },
        "discovery": {
            "n": int(len(selected_discovery_arr)),
            "path": str(SAMPLE_DISCOVERY.relative_to(ROOT)),
            "by_stratum": stratum_breakdown(selected_discovery_arr),
            "is_subset_of_validation": True,
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {MANIFEST}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
