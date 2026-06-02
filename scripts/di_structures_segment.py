#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "tabulate>=0.9"]
# ///
"""Count services per "structure type" bucket, mapping the user's segmentation list
onto data·inclusion's `reseaux_porteurs` codes (+ keyword fallback on `nom`).

Three buckets per the brief:
  T1. Solutions structurées (IAE, EA, GEIQ, PLIE, EPIDE, E2C, APEC, ML, Cap Emploi…)
  T2. Acteurs associatifs ultra-proximité (associations de terrain, structures locales)
  T3. Relais de proximité (CCAS, communes, France Services, CAF, etc.)

A service inherits the highest tier it qualifies for. Output: data/di/structures_segment.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
STRUCT = DATA / "structures.parquet"
SERV = DATA / "services.parquet"
OUT = DATA / "structures_segment.md"

# Buckets defined as (label, list of reseaux_porteurs codes, list of nom keywords).
# Keywords are case-insensitive, full regex (raw — no apostrophes inside char-classes).
BUCKETS_T1 = [
    ("IAE — ACI (Ateliers Chantiers d'Insertion)",            ["aci"], []),
    ("IAE — EI (Entreprise d'Insertion)",                     ["ei"], []),
    ("IAE — ETTI (Entreprise Travail Temp. d'Insertion)",     ["etti"], []),
    ("IAE — EATT",                                            [], [r"\bEATT\b"]),
    ("EA (Entreprise Adaptée)",                               ["ea"], []),
    ("ESAT (handicap)",                                       ["esat"], []),
    ("GEIQ",                                                  ["geiq"], []),
    ("PLIE",                                                  ["plie"], []),
    ("EPIDE",                                                 [], [r"\bEPIDE\b"]),
    ("E2C (École 2e Chance)",                                 [], [r"\bE2C\b", r"[ée]cole\s+de\s+la\s+(?:deuxi|2)[èe]me\s+chance"]),
    ("APEC",                                                  [], [r"\bAPEC\b"]),
    ("Apprentis d'Auteuil",                                   [], [r"apprentis\W{1,3}auteuil"]),
    ("Mission Locale",                                        ["mission-locale"], [r"mission\s+locale"]),
    ("Cap Emploi",                                            ["cap-emploi-reseau-cheops"], [r"cap\s+emploi"]),
    ("AFPA",                                                  ["afpa"], []),
    ("ADIE",                                                  ["adie"], []),
    ("CIDFF (droits femmes/familles)",                        ["cidff"], []),
    ("FJT (Foyers Jeunes Travailleurs)",                      ["residences-fjt"], []),
    ("SPIP (insertion/probation pénitentiaire)",              ["spip"], []),
]

BUCKETS_T2 = [
    ("Associations (générique, fallback nom)",                [], [r"\bassociation\b"]),
    ("CHRS (hébergement / réinsertion sociale)",              ["chrs"], []),
    ("CHU (hébergement urgence)",                             ["chu"], []),
    ("CADA (demandeurs d'asile)",                             ["cada"], []),
    ("CPH (provisoire hébergement)",                          ["cph"], []),
    ("Centre social (fallback nom)",                          [], [r"centre\s+social"]),
    ("Maison de quartier (fallback nom)",                     [], [r"maison\s+de\s+quartier"]),
]

BUCKETS_T3 = [
    ("CCAS / CIAS",                                           ["ccas-cias"], [r"\bCCAS\b", r"\bCIAS\b"]),
    ("CAF",                                                   ["caf"], []),
    ("France Services",                                       ["france-service"], []),
    ("Conseillers numériques",                                ["conseillers-numeriques"], []),
    ("France Travail",                                        ["france-travail"], []),
    ("Communes / Mairies",                                    ["communes"], [r"\bmairie\b", r"\bcommune\s+de\b"]),
    ("Départements",                                          ["departements"], [r"conseil\s+d[ée]partemental"]),
    ("Maisons des Solidarités",                               ["maisons-des-solidarites"], []),
    ("MDPH (autonomie)",                                      ["maison-departementale-de-lautonomie"], []),
    ("CMP (médico-psychologique)",                            ["cmp"], []),
    ("Aidants Connect",                                       ["aidants-connect"], []),
]


def build_match_clause(codes: list[str], name_patterns: list[str]) -> str:
    """Return a SQL boolean expression matching a structure for this bucket."""
    parts = []
    if codes:
        codes_quoted = ", ".join(f"'{c}'" for c in codes)
        parts.append(
            f"(reseaux_porteurs IS NOT NULL AND list_has_any(reseaux_porteurs, [{codes_quoted}]))"
        )
    for p in name_patterns:
        # escape any single quote in the pattern by doubling
        p_safe = p.replace("'", "''")
        parts.append(
            f"regexp_matches(COALESCE(nom, '') || ' ' || COALESCE(description, ''), '(?i){p_safe}')"
        )
    if not parts:
        return "FALSE"
    return "(" + " OR ".join(parts) + ")"


def count_for_bucket(con: duckdb.DuckDBPyConnection, label: str, codes: list[str], name_patterns: list[str]) -> tuple[int, int]:
    clause = build_match_clause(codes, name_patterns)
    n_struct = con.execute(
        f"SELECT COUNT(DISTINCT id) FROM read_parquet('{STRUCT}') WHERE {clause}"
    ).fetchone()[0]
    n_serv = con.execute(
        f"""
        SELECT COUNT(DISTINCT s.id)
        FROM read_parquet('{SERV}') s
        JOIN read_parquet('{STRUCT}') st ON st.id = s.structure_id
        WHERE {clause.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')}
        """
    ).fetchone()[0]
    return n_struct, n_serv


def main() -> int:
    if not STRUCT.exists() or not SERV.exists():
        print("ERR: missing parquets. Run di_prep.py and di_structures_profile.py first.", file=sys.stderr)
        return 1

    con = duckdb.connect()

    out: list[str] = []
    out.append("# Structure-type segmentation — services counts")
    out.append("")
    n_struct = con.execute(f"SELECT COUNT(*) FROM read_parquet('{STRUCT}')").fetchone()[0]
    n_serv = con.execute(f"SELECT COUNT(*) FROM read_parquet('{SERV}')").fetchone()[0]
    n_serv_with_struct = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{SERV}') s WHERE EXISTS (SELECT 1 FROM read_parquet('{STRUCT}') st WHERE st.id = s.structure_id)"
    ).fetchone()[0]
    out.append(f"- Structures: **{n_struct:,}**, services: **{n_serv:,}**")
    out.append(f"- Services with a matching structure (join via structure_id): **{n_serv_with_struct:,}** ({n_serv_with_struct/n_serv:.0%})")
    out.append("")

    for tier_name, buckets in [
        ("Tier 1 — Solutions structurées", BUCKETS_T1),
        ("Tier 2 — Associatifs ultra-proximité", BUCKETS_T2),
        ("Tier 3 — Relais de proximité", BUCKETS_T3),
    ]:
        out.append(f"## {tier_name}")
        out.append("")
        rows = []
        for label, codes, patterns in buckets:
            n_st, n_sv = count_for_bucket(con, label, codes, patterns)
            rows.append([
                label,
                ", ".join(codes) if codes else "—",
                len(patterns) if patterns else 0,
                n_st,
                n_sv,
                f"{n_sv/n_serv:.1%}",
            ])
        out.append(tabulate(
            rows,
            headers=["bucket", "codes", "name regex (n)", "structures", "services", "share corpus"],
            tablefmt="github",
        ))
        out.append("")

    # Tier-rolled-up counts (a service in tier-1 is not also counted in tier-2 etc.)
    out.append("## Cumulative tier coverage (no double-count between tiers)")
    out.append("")
    t1 = " OR ".join([build_match_clause(c, p) for _, c, p in BUCKETS_T1 if (c or p)])
    t2 = " OR ".join([build_match_clause(c, p) for _, c, p in BUCKETS_T2 if (c or p)])
    t3 = " OR ".join([build_match_clause(c, p) for _, c, p in BUCKETS_T3 if (c or p)])
    rows = []
    for label, t1_clause, others_excluded in [
        ("T1 only", t1, ""),
        ("T1 ∪ T2", f"({t1}) OR ({t2})", ""),
        ("T1 ∪ T2 ∪ T3", f"({t1}) OR ({t2}) OR ({t3})", ""),
    ]:
        n = con.execute(
            f"""
            SELECT COUNT(DISTINCT s.id)
            FROM read_parquet('{SERV}') s
            JOIN read_parquet('{STRUCT}') st ON st.id = s.structure_id
            WHERE {t1_clause.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')}
            """
        ).fetchone()[0]
        rows.append([label, n, f"{n/n_serv:.1%}"])
    out.append(tabulate(rows, headers=["scope", "services", "share corpus"], tablefmt="github"))
    out.append("")

    # Out-of-scope structures (no `reseaux_porteurs`, no name match for any bucket)
    out.append("## Services attached to NO bucket (= would be filtered out)")
    out.append("")
    n_out = con.execute(
        f"""
        SELECT COUNT(DISTINCT s.id)
        FROM read_parquet('{SERV}') s
        LEFT JOIN read_parquet('{STRUCT}') st ON st.id = s.structure_id
        WHERE st.id IS NULL OR NOT (
            ({t1.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')}) OR
            ({t2.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')}) OR
            ({t3.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')})
        )
        """
    ).fetchone()[0]
    out.append(f"- **{n_out:,}** services ({n_out/n_serv:.0%}) would be excluded if we keep T1+T2+T3.")
    out.append("")

    # Top sources for out-of-scope services (so we can see what's in the residue)
    rows = con.execute(
        f"""
        SELECT s.source, COUNT(*) AS n
        FROM read_parquet('{SERV}') s
        LEFT JOIN read_parquet('{STRUCT}') st ON st.id = s.structure_id
        WHERE st.id IS NULL OR NOT (
            ({t1.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')}) OR
            ({t2.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')}) OR
            ({t3.replace('reseaux_porteurs', 'st.reseaux_porteurs').replace('nom', 'st.nom').replace('description', 'st.description')})
        )
        GROUP BY s.source
        ORDER BY n DESC
        """
    ).fetchall()
    out.append("Top sources of out-of-scope services:")
    out.append("")
    out.append(tabulate([[s, n] for s, n in rows], headers=["source", "services"], tablefmt="github"))
    out.append("")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
