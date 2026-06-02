#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "tabulate>=0.9"]
# ///
"""Profile the data·inclusion services parquet.

Reads `data/di/services.parquet` (download via di_prep.py) and writes a
human-readable report to `data/di/profile.md`.

Reports:
  - fill rates per field (null / empty / whitespace / non-empty)
  - length distributions (median, p25, p75, p95) for non-empty text fields
  - publics value count distribution
  - cross-tab: publics coverage × publics_precisions presence × description length
  - source breakdown
  - structure type breakdown (if present)

Usage: uv run scripts/di_profile.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
PARQUET = DATA / "services.parquet"
REPORT = DATA / "profile.md"

TEXT_FIELDS = ["publics_precisions", "conditions_acces", "description"]
ARRAY_FIELDS = ["publics"]

DESCRIPTION_LONG_THRESHOLD = 200


def main() -> int:
    if not PARQUET.exists():
        print(f"ERR: {PARQUET} missing. Run scripts/di_prep.py first.", file=sys.stderr)
        return 1

    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{PARQUET}')")
    columns = [c[0] for c in rel.description]

    n_rows = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{PARQUET}')"
    ).fetchone()[0]

    out: list[str] = []
    out.append(f"# Profile — services data·inclusion")
    out.append("")
    out.append(f"- File: `{PARQUET.relative_to(ROOT)}`")
    out.append(f"- Rows: **{n_rows:,}**")
    out.append(f"- Columns: {len(columns)}")
    out.append("")
    out.append("## Columns")
    out.append("")
    out.append("```")
    out.extend(columns)
    out.append("```")
    out.append("")

    # ---- Fill rates for text fields ----
    out.append("## Fill rates — text fields")
    out.append("")
    rows = []
    for f in TEXT_FIELDS:
        if f not in columns:
            rows.append([f, "MISSING", "", "", "", ""])
            continue
        r = con.execute(
            f"""
            SELECT
                SUM(CASE WHEN {f} IS NULL THEN 1 ELSE 0 END) AS null_n,
                SUM(CASE WHEN {f} IS NOT NULL AND length({f}) = 0 THEN 1 ELSE 0 END) AS empty_n,
                SUM(CASE WHEN {f} IS NOT NULL AND length({f}) > 0 AND length(trim({f})) = 0 THEN 1 ELSE 0 END) AS ws_n,
                SUM(CASE WHEN {f} IS NOT NULL AND length(trim({f})) > 0 THEN 1 ELSE 0 END) AS filled_n
            FROM read_parquet('{PARQUET}')
            """
        ).fetchone()
        null_n, empty_n, ws_n, filled_n = r
        rows.append(
            [
                f,
                f"{null_n:,} ({null_n/n_rows:.1%})",
                f"{empty_n:,} ({empty_n/n_rows:.1%})",
                f"{ws_n:,} ({ws_n/n_rows:.1%})",
                f"{filled_n:,} ({filled_n/n_rows:.1%})",
            ]
        )
    out.append(tabulate(rows, headers=["field", "null", "empty", "whitespace", "non-empty"], tablefmt="github"))
    out.append("")

    # ---- Length distributions ----
    out.append("## Length distribution — non-empty text fields")
    out.append("")
    rows = []
    for f in TEXT_FIELDS:
        if f not in columns:
            continue
        r = con.execute(
            f"""
            SELECT
                COUNT(*),
                MIN(length({f})),
                quantile_cont(length({f}), 0.25),
                quantile_cont(length({f}), 0.50),
                quantile_cont(length({f}), 0.75),
                quantile_cont(length({f}), 0.95),
                MAX(length({f}))
            FROM read_parquet('{PARQUET}')
            WHERE {f} IS NOT NULL AND length(trim({f})) > 0
            """
        ).fetchone()
        rows.append([f, *r])
    out.append(tabulate(rows, headers=["field", "n", "min", "p25", "p50", "p75", "p95", "max"], tablefmt="github"))
    out.append("")

    # ---- publics: value-count distribution ----
    out.append("## `publics` — value count when non-empty")
    out.append("")
    if "publics" in columns:
        # publics is likely a list/array
        try:
            r = con.execute(
                f"""
                SELECT
                    SUM(CASE WHEN publics IS NULL OR len(publics) = 0 THEN 1 ELSE 0 END) AS empty_n,
                    SUM(CASE WHEN len(publics) = 1 THEN 1 ELSE 0 END) AS one_n,
                    SUM(CASE WHEN len(publics) = 2 THEN 1 ELSE 0 END) AS two_n,
                    SUM(CASE WHEN len(publics) >= 3 THEN 1 ELSE 0 END) AS three_plus_n
                FROM read_parquet('{PARQUET}')
                """
            ).fetchone()
            empty_n, one_n, two_n, three_plus_n = r
            rows = [
                ["empty / null", empty_n, f"{empty_n/n_rows:.1%}"],
                ["1 value", one_n, f"{one_n/n_rows:.1%}"],
                ["2 values", two_n, f"{two_n/n_rows:.1%}"],
                ["3+ values", three_plus_n, f"{three_plus_n/n_rows:.1%}"],
            ]
            out.append(tabulate(rows, headers=["bucket", "n", "share"], tablefmt="github"))
        except duckdb.Error as e:
            out.append(f"`publics` is not a list/array column or unsupported: {e}")
    else:
        out.append("`publics` column not present.")
    out.append("")

    # ---- publics value frequencies ----
    out.append("## `publics` — top 30 individual values")
    out.append("")
    if "publics" in columns:
        try:
            r = con.execute(
                f"""
                SELECT v AS value, COUNT(*) AS n
                FROM read_parquet('{PARQUET}'),
                     UNNEST(publics) AS t(v)
                GROUP BY v
                ORDER BY n DESC
                LIMIT 30
                """
            ).fetchall()
            out.append(tabulate(r, headers=["value", "n"], tablefmt="github"))
        except duckdb.Error as e:
            out.append(f"Could not unnest `publics`: {e}")
    out.append("")

    # ---- Cross-tab ----
    out.append("## Cross-tab — `publics` coverage × `publics_precisions` presence × `description` length")
    out.append("")
    if "publics" in columns and "publics_precisions" in columns and "description" in columns:
        try:
            r = con.execute(
                f"""
                WITH base AS (
                    SELECT
                        CASE
                            WHEN publics IS NULL OR len(publics) = 0 THEN 'empty'
                            WHEN len(publics) <= 1 THEN 'partial'
                            ELSE 'dense'
                        END AS publics_bucket,
                        CASE
                            WHEN publics_precisions IS NOT NULL AND length(trim(publics_precisions)) > 0
                            THEN 'pp_filled' ELSE 'pp_empty'
                        END AS pp_bucket,
                        CASE
                            WHEN description IS NOT NULL AND length(description) >= {DESCRIPTION_LONG_THRESHOLD}
                            THEN 'desc_long' ELSE 'desc_short'
                        END AS desc_bucket
                    FROM read_parquet('{PARQUET}')
                )
                SELECT publics_bucket, pp_bucket, desc_bucket, COUNT(*) AS n
                FROM base
                GROUP BY ALL
                ORDER BY publics_bucket, pp_bucket, desc_bucket
                """
            ).fetchall()
            rows = [(*row, f"{row[3]/n_rows:.1%}") for row in r]
            out.append(
                tabulate(
                    rows,
                    headers=["publics", "publics_precisions", f"description (≥{DESCRIPTION_LONG_THRESHOLD}c)", "n", "share"],
                    tablefmt="github",
                )
            )
        except duckdb.Error as e:
            out.append(f"Cross-tab failed: {e}")
    out.append("")

    # ---- Stratum sizes for the proposed sampling ----
    out.append("## Stratum sizes (proposed sampling axes)")
    out.append("")
    if "publics_precisions" in columns and "conditions_acces" in columns and "description" in columns:
        try:
            r = con.execute(
                f"""
                WITH base AS (
                    SELECT
                        (publics_precisions IS NOT NULL AND length(trim(publics_precisions)) > 0)
                            OR (conditions_acces IS NOT NULL AND length(trim(conditions_acces)) > 0)
                            OR (description IS NOT NULL AND length(description) >= {DESCRIPTION_LONG_THRESHOLD})
                            AS rich,
                        (publics IS NOT NULL AND len(publics) > 0) AS has_publics
                    FROM read_parquet('{PARQUET}')
                )
                SELECT
                    SUM(CASE WHEN rich THEN 1 ELSE 0 END) AS rich_n,
                    SUM(CASE WHEN NOT rich AND has_publics THEN 1 ELSE 0 END) AS norm_only_n,
                    SUM(CASE WHEN NOT rich AND NOT has_publics THEN 1 ELSE 0 END) AS sparse_n,
                    COUNT(*) AS total_n
                FROM base
                """
            ).fetchone()
            rich_n, norm_only_n, sparse_n, total_n = r
            rows = [
                ["Riche en texte libre", rich_n, f"{rich_n/total_n:.1%}"],
                ["Normalisé sans texte", norm_only_n, f"{norm_only_n/total_n:.1%}"],
                ["Vide partout", sparse_n, f"{sparse_n/total_n:.1%}"],
            ]
            out.append(tabulate(rows, headers=["stratum", "n", "share"], tablefmt="github"))
        except duckdb.Error as e:
            out.append(f"Stratum sizing failed: {e}")
    out.append("")

    # ---- Source breakdown ----
    out.append("## Source breakdown")
    out.append("")
    if "source" in columns:
        r = con.execute(
            f"""
            SELECT source, COUNT(*) AS n
            FROM read_parquet('{PARQUET}')
            GROUP BY source
            ORDER BY n DESC
            """
        ).fetchall()
        rows = [[s, n, f"{n/n_rows:.1%}"] for s, n in r]
        out.append(tabulate(rows, headers=["source", "n", "share"], tablefmt="github"))
    else:
        out.append("`source` column not present.")
    out.append("")

    # ---- score_qualite distribution ----
    out.append("## `score_qualite` — distribution")
    out.append("")
    if "score_qualite" in columns:
        r = con.execute(
            f"""
            SELECT
                SUM(CASE WHEN score_qualite IS NULL THEN 1 ELSE 0 END) AS null_n,
                COUNT(score_qualite) AS non_null_n,
                MIN(score_qualite),
                quantile_cont(score_qualite, 0.05),
                quantile_cont(score_qualite, 0.25),
                quantile_cont(score_qualite, 0.50),
                quantile_cont(score_qualite, 0.75),
                quantile_cont(score_qualite, 0.95),
                MAX(score_qualite),
                AVG(score_qualite)
            FROM read_parquet('{PARQUET}')
            """
        ).fetchone()
        null_n, non_null_n, mn, p05, p25, p50, p75, p95, mx, avg = r
        out.append(f"- null: **{null_n:,}** ({null_n/n_rows:.1%})  \\\n  non-null: **{non_null_n:,}** ({non_null_n/n_rows:.1%})")
        out.append("")
        out.append(
            tabulate(
                [["score_qualite", mn, p05, p25, p50, p75, p95, mx, avg]],
                headers=["field", "min", "p05", "p25", "p50", "p75", "p95", "max", "mean"],
                tablefmt="github",
            )
        )
        out.append("")
        out.append("### Distribution par source")
        out.append("")
        r = con.execute(
            f"""
            SELECT
                source,
                COUNT(*) AS n,
                SUM(CASE WHEN score_qualite IS NULL THEN 1 ELSE 0 END) AS sq_null,
                quantile_cont(score_qualite, 0.50) AS sq_p50,
                AVG(score_qualite) AS sq_mean
            FROM read_parquet('{PARQUET}')
            GROUP BY source
            ORDER BY n DESC
            """
        ).fetchall()
        rows = []
        for s, n, sq_null, sq_p50, sq_mean in r:
            rows.append([
                s, n, f"{sq_null:,} ({sq_null/n:.0%})",
                f"{sq_p50:.3f}" if sq_p50 is not None else "—",
                f"{sq_mean:.3f}" if sq_mean is not None else "—",
            ])
        out.append(tabulate(rows, headers=["source", "n", "score null", "score p50", "score mean"], tablefmt="github"))
        out.append("")
        out.append("### Buckets de qualité (parmi les non-null)")
        out.append("")
        r = con.execute(
            f"""
            WITH bucketed AS (
                SELECT
                    CASE
                        WHEN score_qualite IS NULL THEN 'null'
                        WHEN score_qualite < 0.25 THEN '< 0.25'
                        WHEN score_qualite < 0.50 THEN '0.25 - 0.50'
                        WHEN score_qualite < 0.75 THEN '0.50 - 0.75'
                        ELSE '>= 0.75'
                    END AS bucket
                FROM read_parquet('{PARQUET}')
            )
            SELECT bucket, COUNT(*) AS n
            FROM bucketed
            GROUP BY bucket
            ORDER BY bucket
            """
        ).fetchall()
        rows = [[b, n, f"{n/n_rows:.1%}"] for b, n in r]
        out.append(tabulate(rows, headers=["bucket", "n", "share"], tablefmt="github"))
    else:
        out.append("`score_qualite` column not present.")
    out.append("")

    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
