#!/usr/bin/env python
"""Finalize the LLM-reviewed KU depends_on (P2 human-review step).

Takes the review JSON (verdicts + candidates), applies optional human
overrides (approve/reject by candidate index), rebuilds the baseline
deterministic derived_from edges with the *fixed* node->KU mapping, resolves
real L1 relation ids for derived_from evidence, and writes the final
Neo4j-compatible depends_on JSON.

Final edge set = prior depends (--prior-depends, carried unconditionally)
                 + deterministic derived_from baseline (recomputed from graph)
                 + candidates accepted by the judge (+human approvals)
                 - candidates dropped by human rejections.

Usage:
  python scripts/finalize_ku_depends.py <review.json> <graph.json> <ku.json>
      --out <final.json> [--overrides overrides.json] [--prior-depends prior.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def node_to_ku(ku: dict) -> dict[str, str]:
    """Center priority, contains only fills unmapped (same rule as candidate gen)."""
    n2ku: dict[str, str] = {}
    for u in ku["knowledge_units"]:
        for m in u.get("centers_on", []):
            n2ku[m["id"]] = u["id"]
    for u in ku["knowledge_units"]:
        for m in u.get("contains", []):
            n2ku.setdefault(m["id"], u["id"])
    return n2ku


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Finalize LLM-reviewed KU depends_on.")
    ap.add_argument("review", help="Review JSON (output of review_ku_depends_llm.py).")
    ap.add_argument("graph", help="Layer-1 graph JSON (for relation ids).")
    ap.add_argument("ku", help="KnowledgeUnit JSON (v6).")
    ap.add_argument("--out", required=True, help="Final depends_on JSON.")
    ap.add_argument("--overrides", help="Optional JSON: {\"approve\":[idx],\"reject\":[idx]}.")
    ap.add_argument("--prior-depends", help="Optional prior depends JSON carried forward unconditionally "
                                            "(e.g. the chapter-1 v4 edges when extending to chapter 2).")
    args = ap.parse_args()

    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    ku = json.loads(Path(args.ku).read_text(encoding="utf-8"))
    n2ku = node_to_ku(ku)
    ku_index = {u["id"]: u["name"] for u in ku["knowledge_units"]}

    overrides: dict[str, list[int]] = {"approve": [], "reject": []}
    if args.overrides:
        loaded = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
        overrides = {k: loaded.get(k, []) for k in overrides}

    verdict_by_idx = {v["index"]: v for v in review["verdicts"]}
    cand_by_idx = {i: c for i, c in enumerate(review["candidates"])}

    # resolve relation ids for any derived_from edge (s -> t) in the L1 graph
    rel_id: dict[tuple[str, str], str] = {}
    for r in graph.get("relations", []):
        if r.get("type") == "derived_from":
            rel_id.setdefault((r.get("source_id"), r.get("target_id")), r.get("id", "?"))

    # --- deterministic baseline: cross-KU derived_from edges (fixed mapping) ---
    baseline: dict[tuple[str, str], dict[str, Any]] = {}
    baseline_by_edge: dict[tuple[str, str], str] = {}
    for r in graph.get("relations", []):
        if r.get("type") != "derived_from":
            continue
        s, t = r.get("source_id"), r.get("target_id")
        sk, tk = n2ku.get(s), n2ku.get(t)
        if sk and tk and sk != tk:
            key = (sk, tk)
            rid = r.get("id", "?")
            baseline.setdefault(key, {"derived_from_edges": [], "count": 0})
            if rid not in baseline[key]["derived_from_edges"]:
                baseline[key]["derived_from_edges"].append(rid)
                baseline[key]["count"] += 1
            baseline_by_edge[(s, t, r.get("type"))] = rid

    # --- merge verdicts with overrides ---
    def final_accept(idx: int) -> bool:
        v = verdict_by_idx.get(idx)
        if idx in overrides["reject"]:
            return False
        if idx in overrides["approve"]:
            return True
        return bool(v and v["accept"])

    edges: dict[tuple[str, str], dict[str, Any]] = {}
    dropped_baseline: list[dict[str, Any]] = []

    # prior edges carried forward unconditionally (e.g. chapter-1 v4 when extending)
    if args.prior_depends:
        prior = json.loads(Path(args.prior_depends).read_text(encoding="utf-8"))
        for e in prior.get("depends_on", []):
            key = (e["source_ku"], e["target_ku"])
            if key not in edges:
                edges[key] = {
                    "source_ku": key[0], "target_ku": key[1],
                    "source_name": ku_index.get(key[0], key[0]),
                    "target_name": ku_index.get(key[1], key[1]),
                    "count": e.get("count", 1),
                    "derived_from_edges": e.get("derived_from_edges"),
                    "audit": {"basis": "prior", "judge": None, "evidence": [],
                              "chain_via": [], "prior_from": str(Path(args.prior_depends).resolve())},
                }

    accepted = [i for i, c in enumerate(review["candidates"])
                if final_accept(i) and (i in verdict_by_idx or i in overrides["approve"])]
    for i in accepted:
        c = cand_by_idx[i]
        sk, tk = c["source_ku"], c["target_ku"]
        key = (sk, tk)
        derived = []
        for sig in c.get("signals", []):
            if sig["type"] == "derived_from":
                for e in sig["edges"]:
                    rid = rel_id.get((e["source_id"], e["target_id"])) or baseline_by_edge.get(
                        (e["source_id"], e["target_id"], "derived_from"))
                    if rid and rid not in derived:
                        derived.append(rid)
        entry = {
            "source_ku": sk, "target_ku": tk,
            "source_name": ku_index[sk], "target_name": ku_index[tk],
            "count": len(derived) if derived else (
                c.get("n_edges") or c.get("chain_total") or 1),
            "derived_from_edges": derived or None,
            "audit": {
                "basis": " | ".join(sig["type"] for sig in c.get("signals", [])) or "chain",
                "judge": {"accept": verdict_by_idx[i]["accept"], "strength": verdict_by_idx[i]["strength"],
                          "reason": verdict_by_idx[i]["reason"]} if i in verdict_by_idx else {"human_approve": True},
                "evidence": c.get("signals", []),
                "chain_via": c.get("chain_via", []),
            },
        }
        if key in edges:
            # merge into existing (baseline or earlier accepted) edge
            edges[key]["derived_from_edges"] = list(
                dict.fromkeys((edges[key].get("derived_from_edges") or []) + derived)) or None
            edges[key]["count"] = len(edges[key]["derived_from_edges"] or [])
            edges[key]["audit"] = entry["audit"]
        else:
            edges[key] = entry

    # baseline first unless a rejection dropped the pair entirely
    for key, b in baseline.items():
        if key in edges and key in {(c["source_ku"], c["target_ku"]) for i, c in enumerate(review["candidates"]) if i in overrides["reject"]}:
            dropped_baseline.append({"source": ku_index[key[0]], "target": ku_index[key[1]],
                                     "derived_from_edges": b["derived_from_edges"]})
            continue
        if key not in edges:
            edges[key] = {
                "source_ku": key[0], "target_ku": key[1],
                "source_name": ku_index[key[0]], "target_name": ku_index[key[1]],
                "count": b["count"], "derived_from_edges": b["derived_from_edges"],
                "audit": {"basis": "derived_from (baseline)", "judge": None, "evidence": [], "chain_via": []},
            }

    final_deps = [edges[k] for k in sorted(edges)]
    out = {
        "schema_version": "maf-kg.knowledge-unit.v1.depends_on",
        "knowledge_units": [{"id": u["id"], "name": u["name"]} for u in ku["knowledge_units"]],
        "depends_on": [
            {k: e[k] for k in ("source_ku", "target_ku", "count", "derived_from_edges")
             if k in e and e[k] is not None} for e in final_deps
        ],
        "audit": {
            "edges": final_deps,
            "rejected_by_human": overrides["reject"],
            "approved_by_human": overrides["approve"],
            "dropped_baseline": dropped_baseline,
            "review_json": str(Path(args.review).resolve()),
        },
        "run": dict(review.get("run", {})),
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"final depends_on edges: {len(final_deps)}")
    for e in final_deps:
        print(f"  {e['source_name']} -> {e['target_name']}  "
              f"({e['audit']['basis']}, strength={e['audit'].get('judge') and e['audit']['judge'].get('strength') or '-'})")
    if dropped_baseline:
        print(f"NOTE dropped baseline edges: {len(dropped_baseline)}")
        for d in dropped_baseline:
            print(f"  {d['source']} -> {d['target']}")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())