"""Convergence check: incremental (seeded) vs full re-extraction of the same section.

The incremental path seeds the node/relation store with an earlier section's graph
(--seed-graph) and extracts the target section on top; the full path extracts the
target section from an empty store. Convergence holds if both paths end up with
essentially the same node set (semantic alignment -> Jaccard) and the same gold F1
within run-to-run noise.

Usage:
  python scripts/verify_convergence.py \
      --incremental KG/incremental/section03_r1.json \
      --full "KG/第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r1.json" \
      --seed "KG/第一章第二节_no_thinking.json" \
      --gold evaluation/gold/semantic_first_three_sections_v3.json \
      [--semantic-match]
"""
import argparse
import sys
from pathlib import Path

from evaluate_kg import (
    SemanticNameMatcher,
    canonical_node_name,
    evaluate_nodes_against_gold,
    load_json,
)


def candidate_names(node: dict) -> set[str]:
    names = [node.get("name", "")] + list(node.get("aliases", []))
    return {canonical_node_name(value) for value in names if value}


def align(
    full_nodes: list[dict], inc_nodes: list[dict], semantic: bool
) -> tuple[int, int, list[dict], list[dict]]:
    """Greedy alignment of inc_nodes against full_nodes.

    Rule layer matches canonical name/alias overlap; semantic layer (optional)
    rescues remaining Concepts via embedding retrieval + flash judge. Returns
    (rule_count, semantic_count, full_only, inc_only).
    """
    full_items = [
        {
            "gold_id": node.get("id", ""),
            "name": node.get("name", ""),
            "type": node.get("type", ""),
            "description": node.get("description", ""),
        }
        for node in full_nodes
    ]
    used_full: set[int] = set()
    matched_inc: set[int] = set()
    rule_count = 0
    for i, node in enumerate(inc_nodes):
        names = candidate_names(node)
        if not names:
            continue
        for j, full_node in enumerate(full_nodes):
            if j in used_full:
                continue
            if candidate_names(full_node) & names:
                used_full.add(j)
                matched_inc.add(i)
                rule_count += 1
                break
    sem_count = 0
    if semantic:
        matcher = SemanticNameMatcher()
        available = [
            full_items[j] for j in range(len(full_items)) if j not in used_full
        ]
        for i, node in enumerate(inc_nodes):
            if i in matched_inc or not str(node.get("name", "")).strip():
                continue
            # Unlike the evaluator's gold fallback (Concept-only), the convergence
            # check aligns every node type: Claim name variants are part of the
            # node-set convergence question too.
            query = str(node.get("name", ""))
            q_emb = matcher._embed([query])[0]
            scored = []
            for item in available:
                if item["gold_id"] not in matcher._mem_embed:
                    (emb,) = matcher._embed([str(item.get("name", ""))])
                    matcher._mem_embed[item["gold_id"]] = emb
                scored.append((matcher.cosine(q_emb, matcher._mem_embed[item["gold_id"]]), item))
            scored.sort(reverse=True)
            for sim, candidate in scored:
                if sim < matcher.SIM_THRESHOLD:
                    continue
                if not matcher.judge_same(node, candidate):
                    continue
                j = next(
                    jj
                    for jj, item in enumerate(full_items)
                    if item["gold_id"] == candidate["gold_id"] and jj not in used_full
                )
                used_full.add(j)
                matched_inc.add(i)
                sem_count += 1
                break
    full_only = [full_nodes[j] for j in range(len(full_nodes)) if j not in used_full]
    inc_only = [inc_nodes[i] for i in range(len(inc_nodes)) if i not in matched_inc]
    return rule_count, sem_count, full_only, inc_only


def relation_key(rel: dict) -> str:
    return f"{rel.get('source_id')}|{rel.get('type')}|{rel.get('target_id')}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--incremental", required=True, help="Incremental (seeded) extraction JSON.")
    parser.add_argument("--full", required=True, help="Full re-extraction JSON (empty-store baseline).")
    parser.add_argument("--seed", required=True, help="Seed graph JSON used by the incremental path.")
    parser.add_argument("--gold", required=True, help="Reviewed gold JSON (semantic_first_three_sections_v3.json).")
    parser.add_argument(
        "--semantic-match",
        action="store_true",
        help="Also align node sets with embedding retrieval + flash judge, and use "
        "semantic gold matching for the F1 numbers (requires ollama + DEEPSEEK_API_KEY).",
    )
    args = parser.parse_args()

    inc = load_json(Path(args.incremental))
    full = load_json(Path(args.full))
    seed = load_json(Path(args.seed))
    gold = load_json(Path(args.gold))
    gold_path = Path(args.gold)

    seed_ids = {node["id"] for node in seed.get("nodes", [])}
    inc_new = [node for node in inc.get("nodes", []) if node["id"] not in seed_ids]
    full_nodes = full.get("nodes", [])

    rule_count, sem_count, full_only, inc_only = align(full_nodes, inc_new, args.semantic_match)
    matched = rule_count + sem_count
    union = len(full_nodes) + len(inc_new) - matched
    jaccard = matched / union if union else 0.0

    source_file = Path(str(inc.get("source_path", ""))).name
    has_gold = any(
        str(graph.get("source_file", "")) == source_file for graph in gold.get("graphs", [])
    )
    inc_data = {**inc, "nodes": inc_new}
    full_metrics = None
    inc_metrics = None
    if has_gold:
        full_metrics = evaluate_nodes_against_gold(
            full, gold, gold_path, semantic_match=args.semantic_match
        )["node_metrics"]
        inc_metrics = evaluate_nodes_against_gold(
            inc_data, gold, gold_path, semantic_match=args.semantic_match
        )["node_metrics"]

    seed_relations = seed.get("relations", [])
    inc_relation_keys = {relation_key(rel) for rel in inc.get("relations", [])}
    seed_relation_keys = {relation_key(rel) for rel in seed_relations}

    def metrics_line(label: str, metrics: dict) -> str:
        return (
            f"{label:<13} P {metrics['precision']:.4f}  R {metrics['recall']:.4f}  "
            f"F1 {metrics['f1']:.4f}  (TP {metrics['true_positive']}/"
            f"FP {metrics['false_positive']}/FN {metrics['false_negative']}, "
            f"predicted {metrics['predicted_count']})"
        )

    print(f"== Convergence: {inc.get('source_path', '?')}")
    print(f"Seed graph:    {len(seed.get('nodes', []))} nodes / {len(seed_relations)} relations ({args.seed})")
    print(f"Full path:     {len(full_nodes)} nodes (from empty store)")
    print(f"Incremental:   {len(inc.get('nodes', []))} nodes total, {len(inc_new)} new beyond seed")
    print()
    print("Node set alignment (incremental-new vs full):")
    print(f"  rule matches:      {rule_count}")
    if args.semantic_match:
        print(f"  semantic matches:  {sem_count}")
    print(f"  total matched:     {matched} / {len(inc_new)} (recall of inc) / {len(full_nodes)} (recall of full)")
    print(f"  Jaccard:           {jaccard:.4f}")
    print(f"  full-only:         {len(full_only)} nodes")
    print(f"  inc-only:          {len(inc_only)} nodes")
    print()
    if has_gold:
        print("Gold F1 (section matched by source_path):")
        print(metrics_line("full:", full_metrics))
        print(metrics_line("incremental:", inc_metrics))
    else:
        print(f"Gold F1: skipped (no graph annotation for {source_file!r} in {args.gold})")
    print()
    print("Relations:")
    print(f"  seed: {len(seed_relations)}, incremental total: {len(inc.get('relations', []))}, "
          f"new beyond seed: {len(inc_relation_keys - seed_relation_keys)}, full: {len(full.get('relations', []))}")
    print()
    if full_only:
        print("Full-only nodes (in full path, absent in incremental):")
        for node in sorted(full_only, key=lambda n: n.get("name", ""))[:10]:
            print(f"  {node.get('type', '?'):<8} {node.get('name', '')}")
    if inc_only:
        print("Inc-only nodes (in incremental, absent in full path):")
        for node in sorted(inc_only, key=lambda n: n.get("name", ""))[:10]:
            print(f"  {node.get('type', '?'):<8} {node.get('name', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
