"""Finalise the author review packet.

The draft selected 64 items (four per E1 output, seed 20261011). This turns each
into a self-contained item an author can answer without opening any other file:
the reference item, the source excerpt, what the output actually delivered, the
reviewer's verdict, and the question being asked.

Blinding: method names are replaced by the blind label; the hidden mapping is
written to a separate `_internal` file that must not be handed to the author.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    draft = read(S / "e1_content/author_packet_draft.json")
    blind = read(S / "e1_content/blinding_map_internal.json")["mapping"]
    R = S / "e1_content/review"

    items, rels, corr = {}, {}, {}
    for n in ("A", "B", "C", "D"):
        d = read(R / f"judgements_E1_batch{n}.json")
        for r in d.get("item_rows", []):
            items[(r["run"], r["item_id"])] = r
        for r in d.get("relation_rows", []):
            rels[(r["run"], r["relation_id"])] = r
    c = read(R / "judgements_E1_correction.json")
    for r in c.get("relation_rows", []):
        rels.setdefault((r["run"], r["relation_id"]), {}).update(
            {k: r[k] for k in ("semantic", "strict", "native_signature") if k in r})
    for r in c.get("condition_rows", []):
        if (r["run"], r["item_id"]) in items and "conditions_complete" in r:
            items[(r["run"], r["item_id"])]["conditions_complete"] = r["conditions_complete"]

    # worksheet row lookup for excerpts and native content
    sheets = {}
    for run in blind:
        w = read(R / f"worksheet_{run}.json")
        sheets[run] = {"content": {r["reference_id"]: r for r in w["rows"] if r["kind"] == "content"},
                       "relation": {r["reference_id"]: r for r in w["rows"] if r["kind"] == "relation"}}

    out, mapping = [], {}
    for i, it in enumerate(draft["items"], 1):
        run, rid, stratum = it["run"], it["ref_id"], it["stratum"]
        blind_id = f"AR-{i:02d}"
        row = sheets[run]["content"].get(rid) or sheets[run]["relation"].get(rid) or {}
        entry = {
            "blind_id": blind_id,
            "stratum": stratum,
            "reference_id": rid,
            "reference_name": it.get("ref_name"),
            "section": it.get("section"),
            "source_excerpt": (row.get("source_excerpt") or "")[:1200],
            "reference_meaning": (row.get("reference") or {}).get("meaning"),
            "reference_conditions": (row.get("reference") or {}).get("conditions"),
        }
        if stratum.startswith("content") or stratum == "condition_check":
            j = items.get((run, rid), {})
            entry["output_delivered"] = (row.get("all_native_entities") or [])[:12]
            entry["existing_judgement"] = {
                "coverage": j.get("coverage"),
                "conditions_complete": j.get("conditions_complete"),
                "source_support": j.get("source_support"),
            }
            entry["question"] = (
                "该运行是否真正交付了此项的实质数学含义（含实质限制）？"
                "选项：同意 / 应改为 partial / 应改为 name_only / 应改为 missing / 不确定")
        else:
            j = rels.get((run, rid), {})
            entry["native_edges"] = (row.get("all_native_edges") or [])[:12]
            entry["existing_judgement"] = {
                "semantic": j.get("semantic"), "strict": j.get("strict"),
                "native_signature": j.get("native_signature")}
            entry["question"] = (
                "该运行是否以同一数学对象承载了这条关系？"
                "选项：同意 / 语义应放宽一档 / 语义应收紧一档 / 不确定")
        out.append(entry)
        mapping[blind_id] = {"run": run, "method": blind[run]["method"],
                             "section": blind[run]["section"], "repeat": blind[run]["repeat"]}

    packet = {
        "schema": "maf_kg.www_supplement.author_packet.v1",
        "date": "2026-10-11",
        "seed": 20261011,
        "item_count": len(out),
        "strata": {s: sum(1 for o in out if o["stratum"] == s) for s in
                   ("content_claim", "content_other", "relation_semantic", "condition_check")},
        "blinding": {
            "hidden": ["method name", "run directory", "repeat index"],
            "residual": "the native representation may still reveal the method family; the "
                        "worksheets carry a representation-shape label and the output structure "
                        "itself differs between methods. This residual is disclosed rather than "
                        "hidden, and no method name is printed in this packet.",
            "mapping_file": "author_packet_mapping_internal.json (do not ship with the packet)",
        },
        "allowed_answers": ["同意", "应改判（请在备注写明）", "不确定"],
        "substitution_note": "the plan's fourth stratum (correctness of an actually generated "
                             "mathematical item) would need a separate judging pass; this packet "
                             "substitutes a condition-completeness item and records the deviation",
        "items": out,
    }
    (S / "reports/author_packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (S / "reports/author_packet_mapping_internal.json").write_text(
        json.dumps({"schema": "maf_kg.www_supplement.author_packet.mapping.v1",
                    "note": "hidden mapping; must not be shipped with the packet",
                    "mapping": mapping}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    print(f"packet items: {packet['item_count']}  strata: {packet['strata']}")
    print(f"wrote reports/author_packet.json + author_packet_mapping_internal.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
