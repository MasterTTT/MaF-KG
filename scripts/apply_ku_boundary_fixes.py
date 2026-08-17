#!/usr/bin/env python
"""Apply reviewed boundary fixes to a KnowledgeUnit JSON (deterministic).

Fix list is the outcome of the manual boundary review of
`第一章_knowledge_units_v3.json` (2026-08-18, user-approved):

  FIX-1  merge unit ku_67ad842ddf85 函数的增量 into ku_d7bfee9c0b7f 函数的连续性
         (moved node: concept_函数的增量_1d252d285f5381 -> 连续性.contains)
         Rationale: 连续性定义即 Δx→0 时 Δy→0，函数的增量是连续性的前置概念；
         独立单元仅 1 节点 0 contains，单薄。

  FIX-2  drop node claim_a_x_n_在_x_时的极限_e42de005804849 (a/x^n 在 x→∞ 时的极限)
         from ku_c1f225464a1b 有理分式函数在无穷远处的极限 contains
         Rationale: 例题级局部结论，非独立教学目标；且是有理分式极限公式 m=0 特例。

Fails loudly if expected ids are missing, so the fix list can never silently
drift from the data. Every applied fix is recorded in the output `fixes` array.

Usage:
  python scripts/apply_ku_boundary_fixes.py <ku.json> --out <ku-fixed.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "maf-kg.knowledge-unit.v1"

# (id, action, source_unit_id, target_unit_id or None, node_ids, rationale)
FIXES = [
    {
        "id": "FIX-1",
        "action": "merge_unit",
        "source_unit_id": "ku_67ad842ddf85",      # 函数的增量
        "target_unit_id": "ku_d7bfee9c0b7f",      # 函数的连续性
        "node_ids": ["concept_函数的增量_1d252d285f5381"],
        "rationale": "连续性定义即 Δx→0 时 Δy→0，函数的增量是连续性的前置概念；"
                     "独立单元仅 1 节点 0 contains，单薄。",
    },
    {
        "id": "FIX-2",
        "action": "drop_contains_node",
        "source_unit_id": "ku_c1f225464a1b",      # 有理分式函数在无穷远处的极限
        "target_unit_id": None,
        "node_ids": ["claim_a_x_n_在_x_时的极限_e42de005804849"],  # a/x^n 在 x→∞ 时的极限
        "rationale": "例题级局部结论，非独立教学目标；且为有理分式极限公式 m=0 特例。",
    },
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Apply reviewed boundary fixes to KU JSON.")
    ap.add_argument("input", help="KnowledgeUnit JSON (v3).")
    ap.add_argument("--out", required=True, help="Output fixed KnowledgeUnit JSON.")
    ap.add_argument("--fix-list", default=None,
                    help="Optional JSON file with the fix list (same entry schema as the built-in "
                         "FIXES). Defaults to the built-in list.")
    args = ap.parse_args()

    ku = json.loads(Path(args.input).read_text(encoding="utf-8"))
    units = ku["knowledge_units"]
    by_id = {u["id"]: u for u in units}

    fix_list = FIXES
    if args.fix_list:
        fix_list = json.loads(Path(args.fix_list).read_text(encoding="utf-8"))
        if not isinstance(fix_list, list):
            raise SystemExit("--fix-list must be a JSON array of fix objects")

    applied: list[dict] = []
    for fx in fix_list:
        src = by_id.get(fx["source_unit_id"])
        if src is None:
            raise SystemExit(f"{fx['id']}: source unit {fx['source_unit_id']} not found")
        tgt = by_id.get(fx["target_unit_id"]) if fx["target_unit_id"] else None

        moving: list[dict] = []
        if fx["action"] == "merge_unit":
            if tgt is None:
                raise SystemExit(f"{fx['id']}: target unit {fx['target_unit_id']} not found")
            for nid in fx["node_ids"]:
                node = next((m for m in src.get("centers_on", []) + src.get("contains", [])
                             if m["id"] == nid), None)
                if node is None:
                    raise SystemExit(f"{fx['id']}: node {nid} not in {src['id']}")
                moving.append(node)
            for node in moving:
                tgt["contains"].append(node)
            units.remove(src)
            del by_id[src["id"]]
            applied.append({
                "id": fx["id"], "action": fx["action"],
                "source_unit": f"{src['id']} ({src['name']})",
                "target_unit": f"{tgt['id']} ({tgt['name']})",
                "moved_nodes": [m["id"] for m in moving],
                "rationale": fx["rationale"],
            })
        elif fx["action"] == "drop_contains_node":
            kept = [m for m in src.get("contains", []) if m["id"] not in fx["node_ids"]]
            dropped = len(src.get("contains", [])) - len(kept)
            if dropped != len(fx["node_ids"]):
                raise SystemExit(f"{fx['id']}: expected {len(fx['node_ids'])} drop(s) "
                                 f"in {src['id']}, found {dropped}")
            src["contains"] = kept
            applied.append({
                "id": fx["id"], "action": fx["action"],
                "source_unit": f"{src['id']} ({src['name']})",
                "dropped_nodes": list(fx["node_ids"]),
                "rationale": fx["rationale"],
            })
        else:
            raise SystemExit(f"{fx['id']}: unknown action {fx['action']}")

    ku["fixes"] = applied
    ku["run"]["base_file"] = Path(args.input).name
    ku["run"]["fixes_timestamp"] = datetime.now(timezone.utc).isoformat()

    Path(args.out).write_text(json.dumps(ku, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"units before: 44  after: {len(units)}")
    for a in applied:
        print(f"  {a['id']} {a['action']}: {a.get('source_unit')}"
              + (f" -> {a.get('target_unit')}" if a.get("target_unit") else ""))
    print(f"fixes recorded: {len(applied)}")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())