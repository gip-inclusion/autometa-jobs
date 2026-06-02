#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.0", "pyarrow>=15"]
# ///
"""Pick a stratified seed of ~20 services from discovery_300.parquet for manual
gold-set annotation, and render them as a markdown file with empty YAML
annotation blocks.

Output: data/di/gold_seed.md

The file is meant to be hand-edited. After annotation, parse YAML blocks back
into data/di/gold.jsonl (separate script, not part of this seed step).

Usage: uv run scripts/di_gold_seed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"
SOURCE = DATA / "discovery_300.parquet"
OUT = DATA / "gold_seed.md"

SEED = 20260504
QUOTAS = {"rich": 13, "norm_only": 4, "sparse": 3}  # total 20

RENDER_FIELDS = [
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


def stratum_expr() -> str:
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


def render_value(field: str, value) -> str:
    if value is None:
        return "_(null)_"
    if field == "score_qualite":
        return f"{value:.2f}"
    if isinstance(value, list):
        if not value:
            return "_(empty)_"
        return ", ".join(f"`{v}`" for v in value)
    if isinstance(value, str):
        # Long text fields → blockquote
        if "\n" in value or len(value) > 100:
            quoted = "\n".join("> " + line for line in value.splitlines())
            return "\n" + quoted
        return value
    return str(value)


def render_service(row: dict, idx: int, stratum: str) -> str:
    out: list[str] = []
    out.append(f"## {idx:02d}. {row.get('nom') or '(unnamed)'}")
    out.append("")
    out.append(f"- **stratum**: `{stratum}`")
    out.append(f"- **source**: `{row['source']}`")
    out.append(f"- **score_qualite**: {row['score_qualite']:.2f}")
    out.append(f"- **id**: `{row['id']}`")
    out.append(f"- **type**: {render_value('type', row.get('type'))}")
    out.append(
        f"- **thematiques**: {render_value('thematiques', row.get('thematiques'))}"
    )
    out.append("")
    out.append(f"**publics** (normalisés)  ")
    out.append(render_value("publics", row.get("publics")))
    out.append("")
    out.append(f"**publics_precisions**  ")
    out.append(render_value("publics_precisions", row.get("publics_precisions")))
    out.append("")
    out.append(f"**conditions_acces**  ")
    out.append(render_value("conditions_acces", row.get("conditions_acces")))
    out.append("")
    out.append(f"**description**  ")
    out.append(render_value("description", row.get("description")))
    out.append("")
    out.append("### Gold annotation")
    out.append("")
    out.append("```yaml")
    out.append("age:")
    out.append("  min: null            # int or null. evidence required if non-null")
    out.append("  max: null")
    out.append("  evidence:            # substring proving the bound(s)")
    out.append("    source_field: null  # publics_precisions | description | conditions_acces")
    out.append("    substring: null")
    out.append("")
    out.append("social_minima: []     # subset of: RSA, AAH, ASS, ATA, ASPA, prime-activite, allocation-veuvage, ...")
    out.append("social_minima_evidence: []  # one entry per minimum: {value, source_field, substring}")
    out.append("")
    out.append("unemployment:")
    out.append("  min_months: null    # int or null")
    out.append("  evidence: null")
    out.append("")
    out.append("family_situation: []  # parent-isole, famille-monoparentale, jeune-enfant, grossesse, ...")
    out.append("family_situation_evidence: []")
    out.append("")
    out.append("specific_status: []   # jeune-ase, brsa, demandeur-asile, sans-papiers, sortant-prison, ...")
    out.append("specific_status_evidence: []")
    out.append("")
    out.append("urgency: null         # bool or null. true if service explicitly addresses urgency / sans-abri / mise-a-l-abri")
    out.append("urgency_evidence: null")
    out.append("")
    out.append("conflicts: []         # list of {type: under_specification | contradiction, field, normalized_value, text_evidence, explanation}")
    out.append("")
    out.append("notes: \"\"             # free-form notes for ambiguous cases")
    out.append("```")
    out.append("")
    out.append("---")
    out.append("")
    return "\n".join(out)


def main() -> int:
    if not SOURCE.exists():
        print(f"ERR: {SOURCE} missing. Run scripts/di_prep.py first.", file=sys.stderr)
        return 1

    con = duckdb.connect()
    fields = ", ".join(RENDER_FIELDS)
    sql = f"""
        WITH base AS (
            SELECT {fields}, {stratum_expr()} AS stratum
            FROM read_parquet('{SOURCE}')
        )
        SELECT *,
               row_number() OVER (
                   PARTITION BY stratum
                   ORDER BY hash(id || '{SEED}')
               ) AS rn
        FROM base
    """
    rel = con.execute(sql)
    cols = [c[0] for c in rel.description]
    rows = rel.fetchall()
    by_stratum: dict[str, list[dict]] = {"rich": [], "norm_only": [], "sparse": []}
    for r in rows:
        d = dict(zip(cols, r))
        s = d["stratum"]
        if s in by_stratum:
            by_stratum[s].append(d)
    for s in by_stratum:
        by_stratum[s].sort(key=lambda r: r["rn"])

    picked: list[tuple[str, dict]] = []
    for stratum, n in QUOTAS.items():
        for d in by_stratum[stratum][:n]:
            picked.append((stratum, d))

    out: list[str] = []
    out.append("# Gold-set seed — 20 services pour annotation manuelle")
    out.append("")
    out.append(
        "Généré par `scripts/di_gold_seed.py` à partir de "
        "`data/di/discovery_300.parquet`. Tirage déterministe (seed fixe), "
        "stratifié sur la richesse texte libre."
    )
    out.append("")
    out.append("## Comment annoter")
    out.append("")
    out.append(
        "Pour chaque service, remplir le bloc YAML `Gold annotation`. "
        "Tout champ scalaire DOIT être accompagné d'une `evidence` "
        "(`source_field` + `substring` exacte). Si l'info n'est pas dans le "
        "texte, laisser `null` ou `[]`. Ne pas inférer."
    )
    out.append("")
    out.append("Les champs proposés sont une **hypothèse de schéma a priori** ; "
              "ils seront confrontés au schéma réel découvert par `di-discover`. "
              "Si une mention ne rentre dans aucun champ, la documenter dans `notes`.")
    out.append("")
    out.append("---")
    out.append("")
    for i, (stratum, d) in enumerate(picked, start=1):
        out.append(render_service(d, i, stratum))

    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {OUT} ({len(picked)} services)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
