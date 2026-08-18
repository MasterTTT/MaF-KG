#!/usr/bin/env python
"""Build depends_on between KnowledgeUnits from cross-unit derived_from edges.

Deterministic first pass: for every first-layer derived_from edge whose two
endpoints land in different KnowledgeUnits, emit depends_on(source_ku ->
target_ku). Deduplicated per unit pair, with the underlying edge ids kept for
audit. LLM review is intentionally a separate later step.

NOTE: node->KU mapping uses center priority (a node may be a center in one KU
and a contains member in others; contains must not overwrite centers).

Since 2026-08-18 the LLM-reviewed pipeline supersedes this file for new
experiments: scripts/build_ku_depends_candidates.py + review_ku_depends_llm.py
+ finalize_ku_depends.py (v4). Files named *_v1/v2/v3 are historical outputs of
this deterministic pass.

Usage:
  python scripts/build_ku_depends.py <ku.json> <graph.json> --out <deps.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "maf-kg.knowledge-unit.v1.depends_on"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Build depends_on between KnowledgeUnits.")
    ap.add_argument("ku", help="KnowledgeUnit JSON (v3 output of build_knowledge_units.py).")
    ap.add_argument("graph", help="Layer-1 graph JSON.")
    ap.add_argument("--out", required=True, help="Output depends_on JSON.")
    args = ap.parse_args()

    ku = json.loads(Path(args.ku).read_text(encoding="utf-8"))
    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))

    node_to_ku: dict[str, str] = {}
    for u in ku["knowledge_units"]:
        for m in u.get("centers_on", []):
            node_to_ku[m["id"]] = u["id"]
    for u in ku["knowledge_units"]:
        for m in u.get("contains", []):
            node_to_ku.setdefault(m["id"], u["id"])

    deps: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in graph.get("relations", []):
        if r.get("type") != "derived_from":
            continue
        s = node_to_ku.get(r.get("source_id"))
        t = node_to_ku.get(r.get("target_id"))
        if s and t and s != t:
            deps[(s, t)].append(r.get("id", "?"))

    ku_index = {u["id"]: u["name"] for u in ku["knowledge_units"]}
    edges = [
        {"source_ku": s, "target_ku": t,
         "source_name": ku_index.get(s, s), "target_name": ku_index.get(t, t),
         "derived_from_edges": ids, "count": len(ids)}
        for (s, t), ids in sorted(deps.items())
    ]

    out = {
        "schema_version": SCHEMA,
        "knowledge_units": [{"id": u["id"], "name": u["name"]} for u in ku["knowledge_units"]],
        "depends_on": edges,
        "diagnostics": {"total_edges": len(edges),
                        "note": "deterministic pass from derived_from; LLM review is a separate step"},
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"depends_on edges: {len(edges)}")
    for e in edges:
        print(f"  {e['source_name']} -> {e['target_name']}  ({e['count']})")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
