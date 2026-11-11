"""Score explicit assistant-reviewed alignments; never infer semantic matches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from run_memory_ablation import validate_graph


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_decisions(decisions: list, items: list, valid: set[str]):
    ids = [row["id"] for row in decisions]
    if len(ids) != len(set(ids)) or set(ids) != {row["id"] for row in items}:
        raise ValueError("Review must cover every prediction exactly once")
    if any(row["verdict"] not in valid or not row.get("reason") for row in decisions):
        raise ValueError("Every prediction needs an explicit verdict and reason")


def action_audit(directory: Path, name: str, chunks: list, summaries: list):
    cache = directory / name / "runtime/node_cache/semantic_layer_v1_7_0"
    seen = set()
    creates = reuses = 0
    repeated = []
    unmapped = []
    for chunk, summary in zip(chunks, summaries):
        paths = list(cache.glob(f"*__{chunk['id']}__*.json"))
        if len(paths) != 1:
            raise ValueError("Expected one isolated pre-merge response per chunk")
        actions = read(paths[0])["node_actions"]
        decisions = {row["ref_id"]: row for row in summary["decisions"]}
        for action in actions:
            creates += action["action"] == "create"
            reuses += action["action"] == "reuse"
            decision = decisions.get(action["ref_id"])
            if decision is None:
                unmapped.append({"chunk_id": chunk["id"], "ref_id": action["ref_id"]})
            elif action["action"] == "create" and decision["node_id"] in seen:
                repeated.append({"chunk_id": chunk["id"], "ref_id": action["ref_id"],
                                 "proposed_name": action["name"], "final_id": decision["node_id"]})
        seen.update(row["node_id"] for row in summary["decisions"])
    return {"proposed_create": creates, "proposed_explicit_reuse": reuses,
            "create_against_prior_canonical_id": len(repeated), "recreated_actions": repeated,
            "unmapped_actions": unmapped,
            "meaning": "Counts validated model actions before deterministic merging, not semantic duplicate or reuse correctness."}


def score(directory: Path, review_path: Path, cross_path: Path):
    manifest = read(directory / "manifest.json")
    for artifact in manifest["artifacts"]:
        if sha(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError("Frozen artifact changed before scoring")
    review = read(review_path)
    node_gold_path = directory / "snapshot/evaluation/gold/semantic_first_three_sections_v5.json"
    relation_gold_path = directory / "snapshot/evaluation/gold/relations_section2_v1.json"
    for key, path in (("node_gold", node_gold_path), ("relation_gold", relation_gold_path), ("cross_chunk_reference", cross_path)):
        if review[key + "_sha256"] != sha(path):
            raise ValueError(f"Review hash mismatch: {key}")
    scope = next(g for g in read(node_gold_path)["graphs"] if g["source_file"] == "第一章第二节.md")
    gold = {item["gold_id"]: {**item, "type": group["type"], "requirement": group["requirement"]}
            for group in scope["node_groups"] for item in group["items"]}
    required = {gid for gid, row in gold.items() if row["requirement"] == "M"}
    relations = read(relation_gold_path)["relations"]
    chunks = read(directory / "chunks.json")
    origins = read(cross_path)["introductions"]
    origin_chunk = {gid: next(c["id"] for c in chunks if c["start_line"] <= span["start_line"] <= c["end_line"])
                    for gid, span in origins.items()}
    cross_ids = {r["gold_id"] for r in relations if origin_chunk[r["source_gold_id"]] != origin_chunk[r["target_gold_id"]]}
    result = {"review_status": "assistant preliminary, requires human review; not independent double annotation",
              "review_sha256": sha(review_path), "cross_chunk_reference_sha256": sha(cross_path),
              "validator_sha256": sha(Path(__file__).with_name("run_memory_ablation.py")), "arms": {}}
    for name in ("visible_memory", "no_visible_memory"):
        graph_path = directory / name / "graph.json"
        graph = read(graph_path)
        execution = next(a for a in manifest["arms"] if a["name"] == name)
        validate_graph(graph, manifest, execution, chunks)
        arm = review["arms"][name]
        if arm["graph_sha256"] != sha(graph_path):
            raise ValueError(f"Graph changed: {name}")
        validate_decisions(arm["nodes"], graph["nodes"], {"valid", "modeling_issue", "uncertain"})
        validate_decisions(arm["relations"], graph["relations"], {"valid", "modeling_issue", "uncertain"})
        by_id = {n["id"]: n for n in graph["nodes"]}
        alignments = {n["id"]: n["gold_id"] for n in arm["nodes"] if n.get("gold_id")}
        if len(set(alignments.values())) != len(alignments):
            raise ValueError("Alignment must be one-to-one")
        for decision in arm["nodes"]:
            gid = decision.get("gold_id")
            if gid and (gid not in gold or gold[gid]["requirement"] == "X" or decision["verdict"] != "valid"
                        or gold[gid]["type"] != by_id[decision["id"]]["type"]):
                raise ValueError("Invalid accepted alignment")
        duplicate_groups = arm["duplicate_groups"]
        duplicate_ids = [nid for group in duplicate_groups for nid in group]
        if any(len(group) < 2 for group in duplicate_groups) or len(duplicate_ids) != len(set(duplicate_ids)) or not set(duplicate_ids) <= set(by_id):
            raise ValueError("Invalid or overlapping duplicate groups")
        if len(arm["false_merge_ids"]) != len(set(arm["false_merge_ids"])) or not set(arm["false_merge_ids"]) <= set(by_id):
            raise ValueError("Invalid false merge review")
        accepted_relations = {r["id"] for r in arm["relations"] if r["verdict"] == "valid"}
        edges = {(alignments.get(r["source_id"]), r["type"], alignments.get(r["target_id"]))
                 for r in graph["relations"] if r["id"] in accepted_relations}
        hits = {r["gold_id"] for r in relations if (r["source_gold_id"], r["type"], r["target_gold_id"]) in edges}
        diagnostics = graph["diagnostics"]
        summaries = diagnostics["chunk_summaries"]
        endpoint_ids = {x for r in graph["relations"] for x in (r["source_id"], r["target_id"])}
        result["arms"][name] = {
            "offline_configuration_gate": "passed",
            "original_runner_status": execution["status"],
            "nodes": len(by_id), "relations": len(graph["relations"]),
            "required_node_hits": len(required & set(alignments.values())), "required_node_total": len(required),
            "valid_node_count": sum(n["verdict"] == "valid" for n in arm["nodes"]),
            "node_modeling_issue_count": sum(n["verdict"] == "modeling_issue" for n in arm["nodes"]),
            "uncertain_node_count": sum(n["verdict"] == "uncertain" for n in arm["nodes"]),
            "valid_relation_count": len(accepted_relations),
            "relation_gold_hits": len(hits), "relation_gold_total": len(relations),
            "cross_chunk_gold_hits": len(hits & cross_ids), "cross_chunk_gold_total": len(cross_ids),
            "missing_relation_gold_ids": sorted({r["gold_id"] for r in relations} - hits),
            "duplicate_extra_nodes": sum(len(group) - 1 for group in duplicate_groups),
            "false_merge_count": len(arm["false_merge_ids"]),
            "isolated_node_count": len(set(by_id) - endpoint_ids),
            "chunk_memory_nodes": [row["memory_node_count"] for row in summaries],
            "chunk_memory_relations": [row["memory_relation_count"] for row in summaries],
            "api_summary": diagnostics["api_summary"], "merge_audit": diagnostics["audit"],
            "pre_merge_action_audit": action_audit(directory, name, chunks, summaries),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--cross-reference", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.run_dir, args.review, args.cross_reference)
    (args.run_dir / "reviewed_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
