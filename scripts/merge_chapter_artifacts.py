#!/usr/bin/env python
"""Merge chapter-scoped KG / KU / depends artifacts into single import files.

Cross-chapter use: the Chapter-1 canonical graph (修复版) and the Chapter-2
full graph share node ids for seed nodes; KUs use content-hash ids scoped per
section so they never collide. This script merges:

  * nodes   by id (first file wins on content, later files may add new ids);
  * relations by (source_id, type, target_id) triple;
  * knowledge_units by id (simply concatenated);
  * depends_on by (source_ku, target_ku) pair.

Usage:
  python scripts/merge_chapter_artifacts.py --kg a.json,b.json --ku c.json,d.json \
      --deps e.json,f.json --out-prefix /mnt/e/MaF-KG/KG/merged
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def merge_item_lists(item_lists: list[list[dict]], dedup_key) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for items in item_lists:
        for item in items:
            k = dedup_key(item)
            if k in seen:
                continue
            seen.add(k)
            out.append(item)
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Merge chapter KG/KU/depends artifacts.")
    ap.add_argument("--kg", required=True, help="Comma-separated layer-1 graph JSONs.")
    ap.add_argument("--ku", required=True, help="Comma-separated KnowledgeUnit JSONs.")
    ap.add_argument("--deps", required=True, help="Comma-separated depends_on JSONs.")
    ap.add_argument("--out-prefix", required=True)
    args = ap.parse_args()

    kg_files = [s.strip() for s in args.kg.split(",") if s.strip()]
    ku_files = [s.strip() for s in args.ku.split(",") if s.strip()]
    dep_files = [s.strip() for s in args.deps.split(",") if s.strip()]

    def load(f: str) -> dict:
        return json.loads(Path(f).read_text(encoding="utf-8"))

    # merged layer-1 graph: keep first file's metadata, merge nodes/relations/expressions
    base = load(kg_files[0])
    nodes = merge_item_lists([load(f)["nodes"] for f in kg_files], lambda n: n["id"])
    rels = merge_item_lists([load(f)["relations"] for f in kg_files],
                            lambda r: (r["source_id"], r["type"], r["target_id"]))
    exprs = merge_item_lists([load(f).get("math_expressions", []) for f in kg_files],
                             lambda e: e["id"])
    merged_kg = dict(base)
    merged_kg["nodes"] = nodes
    merged_kg["relations"] = rels
    if exprs:
        merged_kg["math_expressions"] = exprs
    merged_kg["merge"] = {"source_files": kg_files, "merged_at": "merge_chapter_artifacts.py"}

    kus = merge_item_lists([load(f)["knowledge_units"] for f in ku_files], lambda u: u["id"])
    ku_head = load(ku_files[0])
    merged_ku = {
        "schema_version": ku_head.get("schema_version", "maf-kg.knowledge-unit.v1"),
        "source_graphs": ku_head.get("source_graphs", []),
        "knowledge_units": kus,
        "diagnostics": {"merged_from": ku_files,
                        **{k: v for k, v in ku_head.get("diagnostics", {}).items()
                           if k == "invalid_refs"}},
        "run": {"merged": True, "merged_from": ku_files},
    }

    deps = merge_item_lists([load(f).get("depends_on", []) for f in dep_files],
                            lambda e: (e["source_ku"], e["target_ku"]))
    merged_deps = {
        "schema_version": "maf-kg.knowledge-unit.v1.depends_on",
        "knowledge_units": [{"id": u["id"], "name": u["name"]} for u in kus],
        "depends_on": deps,
        "diagnostics": {"merged_from": dep_files, "total_edges": len(deps)},
    }

    kg_out = f"{args.out_prefix}_kg.json"
    ku_out = f"{args.out_prefix}_ku.json"
    dep_out = f"{args.out_prefix}_deps.json"
    Path(kg_out).write_text(json.dumps(merged_kg, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(ku_out).write_text(json.dumps(merged_ku, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(dep_out).write_text(json.dumps(merged_deps, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"KG nodes {len(nodes)} rels {len(rels)} exprs {len(exprs)} -> {kg_out}")
    print(f"KU units {len(kus)} -> {ku_out}")
    print(f"deps edges {len(deps)} -> {dep_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())