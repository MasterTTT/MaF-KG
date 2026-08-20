#!/usr/bin/env python
"""Merge two KnowledgeUnit JSON files (and optional depends_on JSONs) for import.

Chapter runs produce their own KU file and depends_on file. For a cumulative
Neo4j import we want one combined view (e.g. Ch1 KUs + Ch3 KUs; Ch1 depends +
Ch3 depends). This merges deterministically:

  * KUs: concatenate knowledge_units, union source_graphs, keep run metadata of
    base (ku1) extended with an `inputs` note.
  * depends: concatenate depends_on, dedupe by (source_ku, target_ku), union the
    knowledge_units index. Relies on stable content-hash KU ids (no id clash).

Usage:
  python scripts/merge_ku_layers.py \
    --ku1 a.json --ku2 b.json --out-ku merged.json \
    [--deps1 ad.json --deps2 bd.json --out-deps merged_deps.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load(path: str | None) -> dict | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Merge KU and depends_on JSONs across chapters.")
    ap.add_argument("--ku1", required=True)
    ap.add_argument("--ku2", required=True)
    ap.add_argument("--out-ku", required=True)
    ap.add_argument("--deps1")
    ap.add_argument("--deps2")
    ap.add_argument("--out-deps")
    args = ap.parse_args()

    k1, k2 = load(args.ku1), load(args.ku2)
    merged = dict(k1)
    merged["knowledge_units"] = k1["knowledge_units"] + k2["knowledge_units"]
    merged["source_graphs"] = sorted(set(k1.get("source_graphs", []) + k2.get("source_graphs", [])))
    merged["run"] = dict(k1.get("run", {}))
    merged["run"]["inputs"] = [args.ku1, args.ku2]
    Path(args.out_ku).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"KU merged: {len(k1['knowledge_units'])} + {len(k2['knowledge_units'])} = "
          f"{len(merged['knowledge_units'])} -> {args.out_ku}")

    if args.deps1 and args.deps2 and args.out_deps:
        d1, d2 = load(args.deps1), load(args.deps2)
        by_pair: dict[tuple[str, str], dict] = {}
        for edge in d1.get("depends_on", []) + d2.get("depends_on", []):
            key = (edge["source_ku"], edge["target_ku"])
            if key not in by_pair:
                by_pair[key] = dict(edge)
            else:
                cur = by_pair[key]
                cur["count"] = cur.get("count", 0) + edge.get("count", 0)
                cur["derived_from_edges"] = sorted(set(cur.get("derived_from_edges", []) + edge.get("derived_from_edges", [])))
        index: dict[str, str] = {}
        for d in (d1, d2):
            index.update({u["id"]: u["name"] for u in d.get("knowledge_units", [])})
        out = {
            "schema_version": d1.get("schema_version") or d2.get("schema_version"),
            "knowledge_units": sorted(({"id": k, "name": v} for k, v in index.items()),
                                      key=lambda u: u["id"]),
            "depends_on": [by_pair[k] for k in sorted(by_pair)],
            "diagnostics": {"total_edges": len(by_pair),
                            "note": "merged from chapter-level deterministic depends_on passes"},
        }
        Path(args.out_deps).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"depends merged: {len(d1.get('depends_on', []))} + {len(d2.get('depends_on', []))} "
              f"-> {len(by_pair)} unique -> {args.out_deps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
