#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["tabulate>=0.9"]
# ///
"""Compare two extracted.jsonl produced by di-extract pipelines.

Inputs:
  RUN_A : path to extracted.jsonl from a run (e.g. di-extract, rule-based)
  RUN_B : path to extracted.jsonl from another run (e.g. di-extract-strict)

Restricts the comparison to the intersection of service_ids.

Output: data/di/runs/comparison_<A>_vs_<B>.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"


def load_jsonl(path: Path) -> dict[str, dict]:
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARN: skipping bad line in {path}: {e}", file=sys.stderr)
                continue
            sid = rec.get("service_id")
            if sid:
                out[sid] = rec
    return out


def is_filled(value) -> bool:
    """A field is 'filled' iff it's not null and not an empty list."""
    if value is None:
        return False
    if isinstance(value, list) and not value:
        return False
    if isinstance(value, dict) and not value:
        return False
    return True


def normalise_value(v):
    """Normalise a record's value for comparison.

    Scalars-with-evidence become the bare value (we compare values, not evidence).
    Lists of such objects become sorted sets of values.
    Booleans / scalars stay.
    """
    if v is None:
        return None
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, dict) and "value" in item:
                out.append(item["value"])
            else:
                out.append(item)
        return tuple(sorted(out, key=lambda x: str(x)))
    return v


def get_field(rec: dict, group: str, field: str):
    block = rec.get(group, {})
    if not isinstance(block, dict):
        return None
    return block.get(field)


PUBLICS_FIELDS = [
    "age_min", "age_max",
    "situation_familiale", "minima_sociaux", "statut_handicap",
    "protection_juridique",
    "statut_principal", "demandeur_emploi_modalite",
    "type_contrat", "secteur_activite",
    "situation_administrative", "situation_hebergement",
    "statut_jeune", "qualificateurs",
]

PREREQUIS_FIELDS = [
    "inscription_france_travail_requise",
    "prescription_requise",
    "plafond_ressources", "revenu_fiscal_max_eur_par_part",
    "mode_accueil_urgence",
    "residence_geographique",
    "niveau_francais_cecrl_min",
    "besoins_pedagogiques",
]


def compare_field(a_records: dict, b_records: dict, common: list[str], group: str, field: str) -> dict:
    """Returns counts: filled_a, filled_b, both_filled, agree_when_both_filled."""
    filled_a = filled_b = both = agree = 0
    for sid in common:
        a_val = get_field(a_records[sid], group, field)
        b_val = get_field(b_records[sid], group, field)
        a_f = is_filled(a_val)
        b_f = is_filled(b_val)
        if a_f:
            filled_a += 1
        if b_f:
            filled_b += 1
        if a_f and b_f:
            both += 1
            a_n = normalise_value(a_val)
            b_n = normalise_value(b_val)
            if a_n == b_n:
                agree += 1
    return {
        "field": f"{group[:3]}.{field}",
        "filled_a": filled_a,
        "filled_b": filled_b,
        "both": both,
        "agree": agree,
        "agree_pct": (agree / both * 100) if both > 0 else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_a", help="path to extracted.jsonl from run A")
    ap.add_argument("run_b", help="path to extracted.jsonl from run B")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    path_a = Path(args.run_a)
    path_b = Path(args.run_b)
    out_path = Path(args.out) if args.out else DATA / "comparison.md"

    a_records = load_jsonl(path_a)
    b_records = load_jsonl(path_b)
    common = sorted(set(a_records) & set(b_records))
    only_a = sorted(set(a_records) - set(b_records))
    only_b = sorted(set(b_records) - set(a_records))

    out: list[str] = []
    out.append(f"# Comparison — {args.label_a} vs {args.label_b}")
    out.append("")
    out.append(f"- {args.label_a}: `{path_a}` ({len(a_records):,} records)")
    out.append(f"- {args.label_b}: `{path_b}` ({len(b_records):,} records)")
    out.append(f"- Common service_ids: **{len(common):,}**")
    out.append(f"- Only in {args.label_a}: {len(only_a):,}")
    out.append(f"- Only in {args.label_b}: {len(only_b):,}")
    out.append("")

    if not common:
        out.append("No overlap — nothing to compare.")
        out_path.write_text("\n".join(out), encoding="utf-8")
        return 1

    # Coverage
    out.append("## Coverage (≥1 field filled)")
    out.append("")
    cov_a = sum(
        1 for sid in common
        if any(is_filled(get_field(a_records[sid], "publics_extended", f)) for f in PUBLICS_FIELDS)
        or any(is_filled(get_field(a_records[sid], "prerequis_extended", f)) for f in PREREQUIS_FIELDS)
    )
    cov_b = sum(
        1 for sid in common
        if any(is_filled(get_field(b_records[sid], "publics_extended", f)) for f in PUBLICS_FIELDS)
        or any(is_filled(get_field(b_records[sid], "prerequis_extended", f)) for f in PREREQUIS_FIELDS)
    )
    out.append(f"- {args.label_a}: **{cov_a}** / {len(common)} services with ≥1 field filled ({cov_a/len(common):.0%})")
    out.append(f"- {args.label_b}: **{cov_b}** / {len(common)} services ({cov_b/len(common):.0%})")
    out.append("")

    # Per-field comparison
    out.append("## Per-field comparison")
    out.append("")
    rows = []
    for group, fields in [("publics_extended", PUBLICS_FIELDS), ("prerequis_extended", PREREQUIS_FIELDS)]:
        for f in fields:
            r = compare_field(a_records, b_records, common, group, f)
            rows.append([
                r["field"],
                r["filled_a"],
                r["filled_b"],
                r["both"],
                r["agree"],
                f"{r['agree_pct']:.0f}%" if r["agree_pct"] is not None else "—",
            ])
    out.append(tabulate(
        rows,
        headers=[
            "field",
            f"{args.label_a} filled",
            f"{args.label_b} filled",
            "both filled",
            "agree",
            "agree % (when both)",
        ],
        tablefmt="github",
    ))
    out.append("")

    # Conflicts comparison
    out.append("## Conflicts")
    out.append("")
    n_conflicts_a = sum(len(a_records[sid].get("conflicts", []) or []) for sid in common)
    n_conflicts_b = sum(len(b_records[sid].get("conflicts", []) or []) for sid in common)
    services_with_conflict_a = sum(1 for sid in common if a_records[sid].get("conflicts"))
    services_with_conflict_b = sum(1 for sid in common if b_records[sid].get("conflicts"))
    out.append(f"- {args.label_a}: **{n_conflicts_a}** total conflicts across **{services_with_conflict_a}** services")
    out.append(f"- {args.label_b}: **{n_conflicts_b}** total conflicts across **{services_with_conflict_b}** services")
    out.append("")

    # Distinct picks per side
    out.append("## Where they disagree most (top 10 services with the most field-level differences)")
    out.append("")
    diffs_per_service = []
    for sid in common:
        n = 0
        details = []
        for group, fields in [("publics_extended", PUBLICS_FIELDS), ("prerequis_extended", PREREQUIS_FIELDS)]:
            for f in fields:
                a = normalise_value(get_field(a_records[sid], group, f))
                b = normalise_value(get_field(b_records[sid], group, f))
                a_f = a is not None and a != ()
                b_f = b is not None and b != ()
                if a_f or b_f:
                    if a != b:
                        n += 1
                        details.append((f"{group[:3]}.{f}", a, b))
        diffs_per_service.append((n, sid, details))
    diffs_per_service.sort(reverse=True)
    for n, sid, details in diffs_per_service[:10]:
        out.append(f"### {sid} — {n} diffs")
        rows = []
        for field, a, b in details[:8]:
            rows.append([field, str(a)[:50], str(b)[:50]])
        out.append(tabulate(rows, headers=["field", args.label_a, args.label_b], tablefmt="github"))
        out.append("")

    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
