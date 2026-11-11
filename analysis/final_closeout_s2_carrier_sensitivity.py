#!/usr/bin/env python
"""R1 follow-up: extract exactly the rows whose semantic verdict depends on the endpoint-carrier rule.

The two repeats of the same method disagreed because one review agent treated a *nearby* native item
as carrying a reference endpoint while the other required the carrier to land inside the node
review's binding set.  This script does not decide anything; it lists, per graph, every row that is
binding-sensitive, together with the evidence needed to decide it under one unified rule:

  * the reference relation and both endpoint meanings
  * the endpoint binding the node review recorded
  * the native items actually cited by the judgement
  * every native relation that touches either endpoint label set

Read-only.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review/.

Usage:
    python analysis/final_closeout_s2_carrier_sensitivity.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review"
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"

GRAPHS = ["P06", "P05", "P08", "P10", "Q04", "Q02", "V01", "V02"]

# Rows the review agents themselves flagged as sensitive to the endpoint/carrier convention.
FLAGGED = {
    "P08": ["D-R3", "D-R15", "L-R4", "L-R6", "D-R7", "D-R8"],
    "P10": ["D-R3", "D-R4", "D-R5", "D-R6", "D-R10", "D-R1", "D-R16", "L-R3", "L-R8"],
    "P06": ["D-R3", "D-R6", "X-R2", "D-R9", "D-R8"],
    "P05": ["D-R3", "D-R6", "D-R1", "D-R13", "L-R8"],
    "Q04": ["D-R13", "D-R16", "D-R18", "L-R7", "L-R8", "L-R5", "M-R7"],
    "Q02": ["L-R5", "D-R16", "M-R2", "D-R17", "L-R3", "D-R4"],
    "V01": ["D-R10", "D-R15", "D-R16", "D-R1", "D-R6", "M-R9", "X-R2"],
    "V02": ["D-R18", "D-R16", "X-R2", "D-R1", "D-R12", "D-R13", "D-R14", "D-R15"],
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    summary = {}
    for label in GRAPHS:
        worksheet = read_json(OUT_DIR / f"worksheet_v2_{label}.json")
        judgement = read_json(OUT_DIR / f"judgements_{label}.json")
        rows = {row["reference_id"]: row for row in worksheet["rows"]}
        verdicts = {row["reference_id"]: row for row in judgement["judgements"]}
        entities = {
            entity["item_id"]: entity for entity in worksheet.get("native_entities_resolved", [])
        }
        native_by_id = {rel["item_id"]: rel for rel in worksheet.get("native_relations_resolved", [])}

        flagged_rows = []
        for reference_id in FLAGGED.get(label, []):
            row = rows.get(reference_id)
            verdict = verdicts.get(reference_id)
            if not row or not verdict:
                continue
            cited = []
            for item_id in verdict.get("native_item_ids", []):
                if item_id in native_by_id:
                    cited.append({"kind": "relation", **native_by_id[item_id]})
                elif item_id in entities:
                    cited.append({"kind": "entity", **entities[item_id]})
            flagged_rows.append(
                {
                    "reference_id": reference_id,
                    "current_semantic": verdict.get("semantic"),
                    "current_strict": verdict.get("strict"),
                    "current_reason": verdict.get("reason"),
                    "reference_relation": {
                        "type": row["reference_relation"]["type"],
                        "start": row["reference_relation"]["start_node"],
                        "start_name": row["reference_relation"]["start_name"],
                        "start_meaning": row["reference_relation"]["start_meaning"],
                        "end": row["reference_relation"]["end_node"],
                        "end_name": row["reference_relation"]["end_name"],
                        "end_meaning": row["reference_relation"]["end_meaning"],
                        "justification": row["reference_relation"]["justification"],
                    },
                    "endpoint_binding": {
                        "start": {
                            "node": row["endpoint_binding"]["start"]["node"],
                            "status": row["endpoint_binding"]["start"].get("status"),
                            "item_ids": row["endpoint_binding"]["start"].get("item_ids", []),
                            "native_names": row["endpoint_binding"]["start"].get("native_names", []),
                        },
                        "end": {
                            "node": row["endpoint_binding"]["end"]["node"],
                            "status": row["endpoint_binding"]["end"].get("status"),
                            "item_ids": row["endpoint_binding"]["end"].get("item_ids", []),
                            "native_names": row["endpoint_binding"]["end"].get("native_names", []),
                        },
                    },
                    "cited_native_items": cited,
                    "candidate_carriers": {
                        "same_direction": row.get("native_edges_same_direction", [])[:6],
                        "reverse_direction": row.get("native_edges_reverse_direction", [])[:6],
                        "touching_one_endpoint": row.get("native_edges_touching_one_endpoint", [])[:8],
                    },
                }
            )
        summary[label] = {
            "method": judgement.get("method"),
            "repeat": judgement.get("repeat"),
            "flagged_rows": flagged_rows,
            "flagged_count": len(flagged_rows),
        }
        print(f"  {label}: {len(flagged_rows)} flagged rows")

    payload = {
        "schema": "dsh.final_closeout.s2_carrier_sensitivity.v1",
        "purpose": (
            "List the binding-sensitive rows and their evidence so that one endpoint-carrier rule "
            "can be applied to every method consistently. No verdict is changed here."
        ),
        "rule_to_apply": {
            "endpoint_present": (
                "An endpoint counts as present when some native item denotes the same mathematical "
                "object as the reference endpoint, whether or not the node review bound it."
            ),
            "not_enough": "A nearby item with merely similar content does not count; the object must be the same.",
            "carrier": (
                "semantic full/partial requires a native assertion connecting the two ends; both "
                "ends present with no connecting assertion is mentioned_only; an absent end is "
                "not_covered."
            ),
            "strict_unchanged": "The strict column is not affected by this rule.",
        },
        "graphs": summary,
        "total_flagged": sum(block["flagged_count"] for block in summary.values()),
    }
    out = OUT_DIR / "carrier_sensitivity.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO).as_posix()} ({payload['total_flagged']} flagged rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
