"""Score complete explicit reviews of frozen chunk-boundary pairs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_memory_ablation import digest, write_json
from run_chunk_boundary_ablation import ARMS, validate_run
from score_cross_topic_memory_ablation import validate_gold_topic, validate_review_arm, _tier_counts


def score(directory: Path, review_path: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["experiment"] != "chunk_boundary_ablation":
        raise ValueError("Wrong boundary experiment")
    arms = {a["name"]: a for a in manifest["arms"]}
    if set(arms) != set(ARMS) or any(a["status"] != "completed" for a in arms.values()):
        raise ValueError("Both boundary arms must be completed")
    for item in manifest["artifacts"]:
        if digest(Path(item["snapshot"])) != item["sha256"]:
            raise ValueError("Frozen artifact changed")
    if digest(Path(__file__)) != digest(directory / "snapshot/protocol" / Path(__file__).name):
        raise ValueError("Scorer differs from preregistered snapshot")
    gold_path = Path(manifest["gold_snapshot"])
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if digest(gold_path) != manifest["gold_sha256"]:
        raise ValueError("Gold hash mismatch")
    topic = next(t for t in gold["topics"] if t["id"] == manifest["topic"])
    nodes, relations = validate_gold_topic(topic)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (review.get("gold_sha256") != manifest["gold_sha256"] or not review.get("reviewer")
            or set(review.get("arms", {})) != set(ARMS)):
        raise ValueError("Review identity mismatch")
    result = {"topic": topic["id"], "reviewer": review["reviewer"], "review_status": "assistant preliminary explicit review; not human double annotation",
              "gold_sha256": manifest["gold_sha256"], "review_sha256": digest(review_path),
              "scorer_sha256": digest(Path(__file__)), "arms": {}}
    for name in ARMS:
        graph_path = directory / name / "graph.json"
        reviewed = review["arms"][name]
        if digest(graph_path) != arms[name]["graph_sha256"] or digest(graph_path) != reviewed.get("graph_sha256"):
            raise ValueError("Graph hash mismatch")
        for folder in ("agents", "prompts"):
            for path in (directory / "snapshot" / folder).glob("*"):
                if path.is_file() and digest(path) != digest(directory / name / "runtime" / folder / path.name):
                    raise ValueError("Runtime code or prompt changed")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        validate_run(directory, manifest, arms[name], graph)
        decisions, aligned = validate_review_arm(reviewed, graph, nodes)
        required = [n for n in nodes.values() if n["requirement"] == "M"]
        coverage = {}
        for tier in ("core", "detail"):
            ids = {n["gold_id"] for n in required if n["tier"] == tier}
            covered = {i for i in ids if decisions["coverage"][i]["covered"]}
            complete = {i for i in covered if decisions["coverage"][i]["description_complete"]}
            coverage[tier] = {"covered": len(covered), "total": len(ids), "complete_descriptions": len(complete),
                              "missing_gold_ids": sorted(ids - covered)}
        accepted = {r["id"] for r in reviewed["relations"] if r["verdict"] == "valid"}
        edges = {(aligned.get(r["source_id"]), r["type"], aligned.get(r["target_id"])) for r in graph["relations"] if r["id"] in accepted}
        required_relations = [r for r in relations if r["requirement"] == "M"]
        hits = {r["gold_id"] for r in required_relations if (r["source_gold_id"], r["type"], r["target_gold_id"]) in edges}
        endpoints = {n for r in graph["relations"] for n in (r["source_id"], r["target_id"])}
        result["arms"][name] = {
            "chunks": arms[name]["chunk_count"], "nodes": len(graph["nodes"]), "relations": len(graph["relations"]),
            "required_content_coverage": coverage, "exact_entity_hits": _tier_counts(required, set(aligned.values())),
            "strict_M_relation_hits": _tier_counts(required_relations, hits),
            "node_verdicts": {v: sum(n["verdict"] == v for n in reviewed["nodes"]) for v in ("valid", "modeling_issue", "uncertain")},
            "relation_verdicts": {v: sum(n["verdict"] == v for n in reviewed["relations"]) for v in ("valid", "modeling_issue", "uncertain")},
            "duplicate_extra_nodes": sum(len(g) - 1 for g in decisions["duplicate_groups"]),
            "false_merge_count": len(decisions["false_merge_ids"]),
            "isolated_node_count": len({n["id"] for n in graph["nodes"]} - endpoints),
            "api_summary": graph["diagnostics"]["api_summary"],
            "pruned_relations": graph["diagnostics"].get("audit", {}).get("pruned_relations", []),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.run_dir.resolve(), args.review.resolve())
    write_json(args.run_dir / "reviewed_metrics.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
