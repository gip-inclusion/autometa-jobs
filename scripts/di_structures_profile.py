#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "httpx>=0.27", "tabulate>=0.9"]
# ///
"""Download (if needed) the data·inclusion structures parquet and profile it.

Goal: surface the fields and value vocabularies that let us segment services
by the type of structure delivering them (IAE, EA, EATT, PLIE, EPIDE, E2C,
GEIQ, APEC, centres sociaux, communes/mairies, etc.).

Output: data/di/structures_profile.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import httpx
from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
PARQUET = DATA / "structures.parquet"
SERVICES_PARQUET = DATA / "services.parquet"
REPORT = DATA / "structures_profile.md"
DATASET_API = "https://www.data.gouv.fr/api/1/datasets/6233723c2c1e4a54af2f6b2d/"


def find_structures_parquet_url() -> tuple[str, int]:
    r = httpx.get(DATASET_API, timeout=30.0)
    r.raise_for_status()
    payload = r.json()
    candidates = [
        res for res in payload.get("resources", [])
        if res.get("format") == "parquet"
        and "structures" in (res.get("title") or "").lower()
    ]
    if not candidates:
        raise RuntimeError("No structures parquet found")
    candidates.sort(key=lambda res: res.get("last_modified") or "", reverse=True)
    return candidates[0]["url"], int(candidates[0].get("filesize") or 0)


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
                    if pct != last_pct and pct % 10 == 0:
                        print(f"  {pct}%", flush=True)
                        last_pct = pct
    tmp.replace(dest)
    print(f"  done: {dest.stat().st_size:,} bytes")


def main() -> int:
    if not PARQUET.exists():
        url, size = find_structures_parquet_url()
        download(url, PARQUET, size)

    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{PARQUET}') LIMIT 1")
    columns = [c[0] for c in rel.description]
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{PARQUET}')").fetchone()[0]

    out: list[str] = []
    out.append("# Structures profile")
    out.append("")
    out.append(f"- File: `{PARQUET.relative_to(ROOT)}`")
    out.append(f"- Rows: **{n:,}**")
    out.append(f"- Columns: {len(columns)}")
    out.append("")
    out.append("## Columns")
    out.append("")
    out.append("```")
    out.extend(columns)
    out.append("```")
    out.append("")

    # First row sample
    out.append("## First row (sample)")
    out.append("")
    sample = con.execute(f"SELECT * FROM read_parquet('{PARQUET}') LIMIT 1").fetchone()
    rows = []
    for k, v in zip(columns, sample):
        s = repr(v)
        if len(s) > 200:
            s = s[:200] + "…"
        rows.append([k, s])
    out.append(tabulate(rows, headers=["field", "value"], tablefmt="github"))
    out.append("")

    # Source distribution
    if "source" in columns:
        out.append("## Sources")
        out.append("")
        rows = con.execute(
            f"SELECT source, COUNT(*) AS n FROM read_parquet('{PARQUET}') GROUP BY source ORDER BY n DESC"
        ).fetchall()
        out.append(tabulate([[s, n, f"{n/sum(r[1] for r in rows):.1%}"] for s, n in rows], headers=["source", "n", "share"], tablefmt="github"))
        out.append("")

    # Typologie distribution (likely the key field)
    for field in ["typologie", "type", "labels_nationaux", "labels_autres"]:
        if field not in columns:
            continue
        out.append(f"## `{field}` — distribution")
        out.append("")
        # Probe: list/array vs string
        sample_val = con.execute(
            f"SELECT {field} FROM read_parquet('{PARQUET}') WHERE {field} IS NOT NULL LIMIT 1"
        ).fetchone()
        if sample_val is None:
            out.append("_(all null)_")
            out.append("")
            continue
        v = sample_val[0]
        try:
            if isinstance(v, list):
                rows = con.execute(
                    f"""
                    SELECT v AS value, COUNT(*) AS n
                    FROM read_parquet('{PARQUET}'), UNNEST({field}) AS t(v)
                    GROUP BY v
                    ORDER BY n DESC
                    LIMIT 80
                    """
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT {field} AS value, COUNT(*) AS n
                    FROM read_parquet('{PARQUET}')
                    WHERE {field} IS NOT NULL
                    GROUP BY {field}
                    ORDER BY n DESC
                    LIMIT 80
                    """
                ).fetchall()
            out.append(tabulate(rows, headers=["value", "n"], tablefmt="github"))
        except duckdb.Error as e:
            out.append(f"Could not enumerate `{field}`: {e}")
        out.append("")

    # Free-text scan: find structures whose `nom` or `presentation_*` contains
    # keywords from the user's list (IAE, E2C, EPIDE, GEIQ, PLIE, etc.)
    out.append("## Free-text keyword hits in `nom` (top-level structure name)")
    out.append("")
    out.append("Pattern matches in the structure's `nom` column. Useful when typologie doesn't carry the label explicitly.")
    out.append("")
    keywords = [
        ("IAE / SIAE",        r"(?i)\bSIAE\b|\bIAE\b|insertion\s+par\s+l['e]\s*activit"),
        ("EA (Entreprise Adaptée)", r"(?i)\bentreprise[\.s]?\s+adapt[ée]"),
        ("EATT",              r"(?i)\bEATT\b"),
        ("Apprentis d'Auteuil", r"(?i)apprentis\s+d['e]?\s*Auteuil"),
        ("PLIE",              r"(?i)\bPLIE\b"),
        ("EPIDE",             r"(?i)\bEPIDE\b"),
        ("E2C",               r"(?i)\bE2C\b|[ée]cole\s+de\s+la\s+(?:deuxi|2)[èe]me\s+chance"),
        ("GEIQ",              r"(?i)\bGEIQ\b"),
        ("APEC",              r"(?i)\bAPEC\b"),
        ("Mission locale",    r"(?i)mission\s+locale"),
        ("Cap Emploi",        r"(?i)cap\s+emploi"),
        ("Pôle / France Travail", r"(?i)P[ôo]le\s+emploi|France\s+Travail"),
        ("Centre social",     r"(?i)centre\s+social"),
        ("Maison de quartier", r"(?i)maison\s+de\s+quartier"),
        ("CCAS / CIAS",       r"(?i)\bCCAS\b|\bCIAS\b"),
        ("Mairie / Commune",  r"(?i)\bmairie\b|\bcommune\s+de\b"),
        ("Département",       r"(?i)\bd[ée]partement\b|\bconseil\s+d[ée]partemental"),
        ("Intercommunalité",  r"(?i)\bcommunaut[ée]\s+(?:de\s+communes|d['a]gglom[ée]ration|urbaine)\b|\bm[ée]tropole\b"),
        ("Région",            r"(?i)\br[ée]gion\b\s+\w"),
        ("Association",       r"(?i)\bassociation\b"),
    ]
    rows = []
    for label, pattern in keywords:
        try:
            count = con.execute(
                f"""
                SELECT COUNT(DISTINCT id)
                FROM read_parquet('{PARQUET}')
                WHERE regexp_matches(COALESCE(nom, '') || ' ' || COALESCE(presentation_resume, '') || ' ' || COALESCE(presentation_detail, ''), '{pattern}')
                """
            ).fetchone()[0]
        except duckdb.Error:
            try:
                count = con.execute(
                    f"""
                    SELECT COUNT(DISTINCT id)
                    FROM read_parquet('{PARQUET}')
                    WHERE regexp_matches(COALESCE(nom, ''), '{pattern}')
                    """
                ).fetchone()[0]
            except duckdb.Error as e:
                count = -1
        rows.append([label, count, f"{count/n:.1%}" if count >= 0 else "—"])
    out.append(tabulate(rows, headers=["keyword group", "structures", "share"], tablefmt="github"))
    out.append("")

    # If services parquet is present, count services per structure-type bucket
    if SERVICES_PARQUET.exists():
        out.append("## Services × structures join — services per bucket")
        out.append("")
        out.append("Joining via `structure_id` (services.structure_id == structures.id).")
        out.append("Services counted only once even if a structure matches several keyword groups (the join is per-keyword).")
        out.append("")
        rows = []
        for label, pattern in keywords:
            try:
                count = con.execute(
                    f"""
                    SELECT COUNT(DISTINCT s.id)
                    FROM read_parquet('{SERVICES_PARQUET}') s
                    JOIN read_parquet('{PARQUET}') st ON st.id = s.structure_id
                    WHERE regexp_matches(COALESCE(st.nom, '') || ' ' || COALESCE(st.presentation_resume, '') || ' ' || COALESCE(st.presentation_detail, ''), '{pattern}')
                    """
                ).fetchone()[0]
                rows.append([label, count])
            except duckdb.Error as e:
                rows.append([label, f"err: {e}"])
        out.append(tabulate(rows, headers=["keyword group", "services"], tablefmt="github"))
        out.append("")

    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
