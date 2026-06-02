#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["tabulate>=0.9"]
# ///
"""Merge all T1 chunk outputs (incl. resume) into one deduplicated JSONL +
produce a final stats report.

Inputs: data/di/runs/<rid>/extracted.jsonl for each run id we know about.
Output:
  - data/di/t1_extracted.jsonl (deduplicated by service_id)
  - data/di/t1_final_report.md (stats)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "di"

# Run IDs to merge, in priority order (later = wins for duplicates)
RUNS = [
    ("C1 (timeout 85%)",  "<run-id>"),
    ("C2 (timeout 62%)",  "<run-id>"),
    ("C3 (clean 100%)",   "<run-id>"),
    ("C4 (sub-agents 100%)", "<run-id>"),
    ("Resume C1+C2",      "<run-id>"),
]


def load_records(rid: str) -> list[dict]:
    path = DATA / "runs" / rid / "extracted.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("service_id"):
                    out.append(rec)
            except json.JSONDecodeError:
                continue
    return out


def is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, list) and not value:
        return False
    if isinstance(value, dict) and not value:
        return False
    return True


def main() -> int:
    by_id: dict[str, dict] = {}
    counts_per_run: list[tuple[str, int]] = []
    for label, rid in RUNS:
        recs = load_records(rid)
        counts_per_run.append((label, len(recs)))
        for r in recs:
            by_id[r["service_id"]] = r

    # Verify against expected T1 set
    expected = set()
    for line in (DATA / "t1_services.jsonl").open(encoding="utf-8"):
        expected.add(json.loads(line).get("id"))

    matched = set(by_id) & expected
    missing = expected - set(by_id)
    extra = set(by_id) - expected

    out_path = DATA / "t1_extracted.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for sid in sorted(by_id):
            f.write(json.dumps(by_id[sid], ensure_ascii=False) + "\n")

    # Stats
    stats: dict[str, Counter] = defaultdict(Counter)
    n_with_filled_publics = 0
    n_with_filled_prerequis = 0
    n_with_conflicts = 0
    n_total_conflicts = 0
    conflicts_by_field: Counter = Counter()
    conflicts_by_type: Counter = Counter()

    publics_fields = [
        "age_min", "age_max", "situation_familiale", "minima_sociaux",
        "statut_handicap", "protection_juridique", "statut_principal",
        "demandeur_emploi_modalite", "type_contrat", "secteur_activite",
        "situation_administrative", "situation_hebergement", "statut_jeune",
        "qualificateurs",
    ]
    prerequis_fields = [
        "inscription_france_travail_requise", "prescription_requise",
        "plafond_ressources", "revenu_fiscal_max_eur_par_part",
        "mode_accueil_urgence", "residence_geographique",
        "niveau_francais_cecrl_min", "besoins_pedagogiques",
    ]

    for rec in by_id.values():
        pe = rec.get("publics_extended") or {}
        pr = rec.get("prerequis_extended") or {}
        if any(is_filled(pe.get(f)) for f in publics_fields):
            n_with_filled_publics += 1
        if any(is_filled(pr.get(f)) for f in prerequis_fields):
            n_with_filled_prerequis += 1
        for f in publics_fields:
            v = pe.get(f)
            if is_filled(v):
                stats[f"pub.{f}"]["n"] += 1
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and "value" in item:
                            stats[f"pub.{f}"][f"v:{item['value']}"] += 1
                        elif isinstance(item, str):
                            stats[f"pub.{f}"][f"v:{item}"] += 1
                elif isinstance(v, dict) and "value" in v:
                    stats[f"pub.{f}"][f"v:{v['value']}"] += 1
                elif isinstance(v, (str, int, bool)):
                    stats[f"pub.{f}"][f"v:{v}"] += 1
        for f in prerequis_fields:
            v = pr.get(f)
            if is_filled(v):
                stats[f"pre.{f}"]["n"] += 1
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and "value" in item:
                            stats[f"pre.{f}"][f"v:{item['value']}"] += 1
                        elif isinstance(item, str):
                            stats[f"pre.{f}"][f"v:{item}"] += 1
                elif isinstance(v, dict) and "value" in v:
                    stats[f"pre.{f}"][f"v:{v['value']}"] += 1
                elif isinstance(v, (str, int, bool)):
                    stats[f"pre.{f}"][f"v:{v}"] += 1

        confs = rec.get("conflicts") or []
        if confs:
            n_with_conflicts += 1
            n_total_conflicts += len(confs)
            for c in confs:
                conflicts_by_field[c.get("field", "?")] += 1
                conflicts_by_type[c.get("type", "?")] += 1

    out: list[str] = []
    out.append("# T1 Final Report")
    out.append("")
    out.append("## Source runs")
    out.append("")
    rows = [[label, n] for label, n in counts_per_run]
    rows.append(["TOTAL records loaded (pre-dedup)", sum(n for _, n in counts_per_run)])
    rows.append(["Distinct service_ids after dedup", len(by_id)])
    out.append(tabulate(rows, headers=["run", "records"], tablefmt="github"))
    out.append("")

    out.append(f"- Expected T1 set: **{len(expected):,}** services")
    out.append(f"- Matched (extracted): **{len(matched):,}** ({len(matched)/len(expected):.1%})")
    out.append(f"- Missing (no extraction): **{len(missing):,}**")
    out.append(f"- Extra (in output but not in T1): **{len(extra):,}**")
    out.append("")

    out.append("## Coverage")
    out.append("")
    out.append(f"- Services with ≥1 publics_extended field filled: **{n_with_filled_publics:,}** ({n_with_filled_publics/len(by_id):.1%})")
    out.append(f"- Services with ≥1 prerequis_extended field filled: **{n_with_filled_prerequis:,}** ({n_with_filled_prerequis/len(by_id):.1%})")
    out.append(f"- Services with conflicts: **{n_with_conflicts:,}** ({n_with_conflicts/len(by_id):.1%})")
    out.append(f"- Total conflicts: **{n_total_conflicts:,}**")
    out.append("")

    out.append("## Per-field fill counts")
    out.append("")
    rows = []
    for f in publics_fields:
        n = stats[f"pub.{f}"].get("n", 0)
        top = sorted([(k[2:], v) for k, v in stats[f"pub.{f}"].items() if k.startswith("v:")], key=lambda x: -x[1])[:5]
        top_s = ", ".join(f"{k} {v}" for k, v in top) if top else ""
        rows.append([f"pub.{f}", n, top_s])
    for f in prerequis_fields:
        n = stats[f"pre.{f}"].get("n", 0)
        top = sorted([(k[2:], v) for k, v in stats[f"pre.{f}"].items() if k.startswith("v:")], key=lambda x: -x[1])[:5]
        top_s = ", ".join(f"{k} {v}" for k, v in top) if top else ""
        rows.append([f"pre.{f}", n, top_s])
    out.append(tabulate(rows, headers=["field", "n", "top values"], tablefmt="github"))
    out.append("")

    out.append("## Conflicts breakdown")
    out.append("")
    out.append("By field:")
    out.append("")
    out.append(tabulate(
        sorted(conflicts_by_field.items(), key=lambda x: -x[1])[:20],
        headers=["field", "n"],
        tablefmt="github",
    ))
    out.append("")
    out.append("By type:")
    out.append("")
    out.append(tabulate(
        sorted(conflicts_by_type.items(), key=lambda x: -x[1]),
        headers=["type", "n"],
        tablefmt="github",
    ))
    out.append("")

    if missing:
        out.append("## Missing services")
        out.append("")
        out.append(f"{len(missing):,} services from the T1 set have no extracted record.")
        out.append("")
        out.append("First 10:")
        out.append("```")
        for sid in list(missing)[:10]:
            out.append(f"  {sid}")
        out.append("```")
        out.append("")

    report_path = DATA / "t1_final_report.md"
    report_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {report_path}")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes, {len(by_id)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
