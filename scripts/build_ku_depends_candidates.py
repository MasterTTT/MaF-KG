#!/usr/bin/env python
"""Generate depends_on candidates between KnowledgeUnits (deterministic pass).

Signals (all cross-KU, i.e. the two endpoint nodes live in different units):

  * derived_from           : target is derived from source -> source KU depends on target KU
  * defined_in_terms_of    : target is used to define source -> source KU depends on target KU
  * uses                   : method uses some knowledge -> method KU depends on knowledge KU
  * involves (weak)        : only kept when the involved node is a *center* of the
                             target KU and the target KU is not a generic foundation
                             bucket (映射 / 函数的基本概念) - otherwise it is content
                             similarity, not prerequisite.
  * 2-hop chains           : A -> B -> C every hop cross-KU (derived_from /
                             defined_in_terms_of / uses), emitting A depends C so the
                             LLM can review edges like 连续性 -> 极限定义 that the raw
                             edges only express transitively.

special_case_of is deliberately NOT a signal (subsumption != prerequisite).

Output keeps the Neo4j-compatible depends_on shape (source_ku/target_ku/count/
derived_from_edges) and rich per-candidate audit fields for the LLM review step.

Usage:
  python scripts/build_ku_depends_candidates.py <ku.json> <graph.json> --out <candidates.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "maf-kg.knowledge-unit.v1.depends_on.candidates"

# Generic foundation buckets: every later KU "involves" these, so cross edges into
# them are curriculum-wide anchors, not pair-specific prerequisites.
FOUNDATION_KUS = {"映射", "函数的基本概念"}

# Strong signal types: express "target is part of the meaning/premise of source".
STRONG_TYPES = {"derived_from", "defined_in_terms_of", "uses"}


def node_to_ku(ku: dict) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (node_to_ku, center_of, name_of_ku).

    A node may be a center in one KU and a contains member in several others;
    membership must NOT be overwritten by later units. Centers take priority,
    contains only fill nodes that are not yet mapped.
    """
    n2ku: dict[str, str] = {}
    center_of: dict[str, str] = {}
    name_of: dict[str, str] = {}
    for u in ku["knowledge_units"]:
        name_of[u["id"]] = u["name"]
        for m in u.get("centers_on", []):
            n2ku[m["id"]] = u["id"]
            center_of[m["id"]] = u["id"]
    for u in ku["knowledge_units"]:
        for m in u.get("contains", []):
            n2ku.setdefault(m["id"], u["id"])
    return n2ku, center_of, name_of


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Generate KU depends_on candidates (deterministic).")
    ap.add_argument("ku", help="KnowledgeUnit JSON (v6 output of build_knowledge_units.py).")
    ap.add_argument("graph", help="Layer-1 graph JSON.")
    ap.add_argument("--out", required=True, help="Output candidates JSON.")
    args = ap.parse_args()

    ku = json.loads(Path(args.ku).read_text(encoding="utf-8"))
    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    n2ku, center_of, name_of = node_to_ku(ku)

    nodes = {n["id"]: n for n in graph["nodes"]}

    # Aggregated pair candidates: (src_ku, tgt_ku) -> details
    pair: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "types": defaultdict(list), "n_edges": 0, "direct": True, "chain_via": []})

    def add_edge(typ: str, s: str, t: str):
        sk, tk = n2ku[s], n2ku[t]
        if name_of[tk] in FOUNDATION_KUS:
            # Target is a public foundation bucket (映射 / 函数的基本概念): every later
            # unit presupposes it, so it carries no pair-specific prerequisite signal.
            return
        d = pair[(sk, tk)]
        d["types"][typ].append((s, t))
        d["n_edges"] += 1

    for r in graph.get("relations", []):
        typ = r.get("type")
        s, t = r.get("source_id"), r.get("target_id")
        if s not in n2ku or t not in n2ku:
            continue
        sk, tk = n2ku[s], n2ku[t]
        if sk == tk:
            continue
        if typ in STRONG_TYPES:
            add_edge(typ, s, t)
        elif typ == "involves":
            # weak anchor: keep only when the involved node is the target KU's center
            # (and the target KU is not a generic foundation bucket - handled above).
            if t in center_of:
                add_edge("involves(center)", s, t)
        # special_case_of and plain involves are ignored.

    # whitelist: the single informative foundation-to-foundation edge
    # (函数的基本概念 is defined in terms of 映射 - the chapter's opening topic).
    whitelist = ("函数的基本概念", "映射")
    src_ku = tgt_ku = None
    for u in ku["knowledge_units"]:
        if u["name"] == whitelist[0]:
            src_ku = u["id"]
        if u["name"] == whitelist[1]:
            tgt_ku = u["id"]
    if src_ku and tgt_ku:
        d = pair[(src_ku, tgt_ku)]
        if not d["types"]:
            # find a real defined_in_terms_of edge spanning the two units (for audit)
            real = None
            for r in graph.get("relations", []):
                if (r.get("type") == "defined_in_terms_of"
                        and n2ku.get(r.get("source_id")) == src_ku
                        and n2ku.get(r.get("target_id")) == tgt_ku):
                    real = (r.get("source_id"), r.get("target_id"))
                    break
            d["types"]["defined_in_terms_of"] = [real or ("(whitelist)", "(whitelist)")]
            d["n_edges"] = 1

    # 2-hop chains over strong edges: A -> B -> C, all three KUs pairwise distinct.
    # Only emitted when the pair has NO direct signal (avoids redundancy), the mid hop
    # is not a foundation bucket, and the chain target is not a foundation bucket.
    hop_edges: list[tuple[str, str, str, str]] = []  # (src_ku, tgt_ku, src_node, tgt_node)
    for r in graph.get("relations", []):
        typ = r.get("type")
        s, t = r.get("source_id"), r.get("target_id")
        if s not in n2ku or t not in n2ku:
            continue
        sk, tk = n2ku[s], n2ku[t]
        if sk != tk and typ in STRONG_TYPES and name_of[tk] not in FOUNDATION_KUS:
            hop_edges.append((sk, tk, s, t))

    by_src: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for sk, tk, s, t in hop_edges:
        by_src[sk].append((tk, s, t))

    for sk, tk, s, t in hop_edges:
        d = pair.get((sk, tk))
        has_direct = d is not None and len(d["types"]) > 0
        for tk2, s2, t2 in by_src.get(tk, []):
            if tk2 in (sk, tk):  # avoid A->B->A cycles and self loops
                continue
            if name_of[tk2] in FOUNDATION_KUS:
                continue
            d2 = pair[(sk, tk2)]
            if d2["types"]:  # pair already has a direct signal; chain adds nothing
                continue
            d2["chain_via"].append({"mid_ku": tk, "hop1": (s, t), "hop2": (s2, t2)})

    ku_index = {u["id"]: u["name"] for u in ku["knowledge_units"]}
    candidates: list[dict[str, Any]] = []
    for (sk, tk), d in sorted(pair.items()):
        if len(d["types"]) == 0 and not d["chain_via"]:
            continue
        types: list[str] = []
        for typ, edges in d["types"].items():
            types.append({"type": typ, "count": len(edges),
                          "edges": [{"source_id": s, "target_id": t,
                                     "source_name": nodes[s]["name"], "target_name": nodes[t]["name"]}
                                    for s, t in edges]})
        chains = [{"mid_ku": c["mid_ku"], "mid_name": ku_index.get(c["mid_ku"], c["mid_ku"]),
                   "hop1": list(c["hop1"]), "hop2": list(c["hop2"]),
                   "hop1_names": [nodes[c["hop1"][0]]["name"], nodes[c["hop1"][1]]["name"]],
                   "hop2_names": [nodes[c["hop2"][0]]["name"], nodes[c["hop2"][1]]["name"]]}
                  for c in d["chain_via"][:3]]
        candidates.append({
            "source_ku": sk,
            "target_ku": tk,
            "source_name": ku_index[sk],
            "target_name": ku_index[tk],
            "signals": types,
            "chain_via": chains,
            "chain_total": len(d["chain_via"]),
            "n_edges": d["n_edges"],
        })

    # keep Neo4j-compatible summary fields at top level of a parallel list
    out = {
        "schema_version": SCHEMA,
        "knowledge_units": [{"id": u["id"], "name": u["name"]} for u in ku["knowledge_units"]],
        "candidates": candidates,
        "diagnostics": {
            "total_candidates": len(candidates),
            "with_chain": sum(1 for c in candidates if c["chain_via"]),
            "signal_types": sorted({s["type"] for c in candidates for s in c["signals"]}),
            "note": "deterministic candidate pass; LLM semantic review is the next step",
        },
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"candidates: {len(candidates)}")
    for c in candidates:
        sig = "+".join(f"{s['type']}x{s['count']}" for s in c["signals"]) or "chain-only"
        ch = f"  [chain:{len(c['chain_via'])}]" if c["chain_via"] else ""
        print(f"  {c['source_name']} -> {c['target_name']}  ({sig}){ch}")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())