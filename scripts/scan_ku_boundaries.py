#!/usr/bin/env python
"""Boundary scan for KnowledgeUnit JSON (deterministic review aid).

Flags units that are small enough to be suspect and may need human review
(merge / drop / split). The scan is deliberately dumb: it reports structure,
not semantics. Thresholds are CLI-configurable.

  * units with total nodes <= --max-small (default 2) are listed as SMALL;
  * units with a single node and no contains are listed as SINGLETON;
  * units whose centers_on count exceeds --max-centers (default 3) are listed
    as CENTER-HEAVY (prompt asks for 1-3 centers).

Usage:
  python scripts/scan_ku_boundaries.py <ku.json> [--max-small 2] [--max-centers 3]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Boundary scan for KnowledgeUnit JSON.")
    ap.add_argument("ku", help="KnowledgeUnit JSON (maf-kg.knowledge-unit.v1).")
    ap.add_argument("--max-small", type=int, default=2, help="Max total nodes to flag as SMALL.")
    ap.add_argument("--max-centers", type=int, default=3, help="Max centers_on to flag as CENTER-HEAVY.")
    args = ap.parse_args()

    ku = json.loads(Path(args.ku).read_text(encoding="utf-8"))
    units = ku.get("knowledge_units", [])
    rows = []
    for u in units:
        n_center = len(u.get("centers_on", []))
        n_contain = len(u.get("contains", []))
        rows.append({
            "id": u["id"], "name": u.get("name", ""),
            "centers": n_center, "contains": n_contain, "total": n_center + n_contain,
        })

    small = [r for r in rows if r["total"] <= args.max_small]
    singleton = [r for r in rows if r["total"] == 1 and r["contains"] == 0]
    heavy = [r for r in rows if r["centers"] > args.max_centers]

    print(f"units: {len(rows)}")
    print(f"SMALL (total <= {args.max_small}): {len(small)}")
    for r in sorted(small, key=lambda r: r["total"]):
        print(f"  {r['id']}  centers={r['centers']} contains={r['contains']}  {r['name']}")
    print(f"SINGLETON (1 node, no contains): {len(singleton)}")
    for r in singleton:
        print(f"  {r['id']}  {r['name']}")
    print(f"CENTER-HEAVY (centers > {args.max_centers}): {len(heavy)}")
    for r in heavy:
        print(f"  {r['id']}  centers={r['centers']}  {r['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
