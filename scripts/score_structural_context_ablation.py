"""Score explicit, complete review annotations for structural metadata pairs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_memory_ablation import digest, validate_graph, write_json
from run_structural_context_ablation import ARMS, validate_requests
from score_cross_topic_memory_ablation import validate_gold_topic, validate_review_arm, _tier_counts


def forbidden_metadata(value: object) -> bool:
    forbidden = {"heading_path", "teaching_block_id", "teaching_block_ids", "teaching_blocks", "discourse_role"}
    if isinstance(value, dict):
        return bool(forbidden.intersection(value)) or any(forbidden_metadata(v) for v in value.values())
    if isinstance(value, list):
        return any(forbidden_metadata(v) for v in value)
    return False


def validate_request_audit(directory: Path, name: str, graph: dict) -> None:
    prompt_hash = digest(directory / "snapshot/prompts/extraction_system.txt")
    for summary in graph["diagnostics"]["chunk_summaries"]:
        path = directory / name / "requests" / (summary["chunk_id"] + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["system_prompt_sha256"] != prompt_hash:
            raise ValueError("Request system prompt hash mismatch")
        sent = record["sent_payload"]
        if (len(sent["node_memory"]) != summary["memory_node_count"] or
                len(sent["relation_memory"]) != summary["memory_relation_count"]):
            raise ValueError("Recorded memory differs from extraction diagnostics")
        for key in ("defined_term_hints", "source_latex_candidates", "node_memory", "relation_memory"):
            if forbidden_metadata(sent[key]):
                raise ValueError(f"Structural metadata leaked through {key}")


def score(directory: Path, review_path: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["experiment"] != "explicit_structural_metadata_ablation":
        raise ValueError("Wrong experiment")
    executions = {arm["name"]: arm for arm in manifest["arms"]}
    if set(executions) != set(ARMS) or any(arm["status"] != "completed" for arm in executions.values()):
        raise ValueError("Both structural arms must be completed")
    for item in manifest["artifacts"]:
        if digest(Path(item["snapshot"])) != item["sha256"]:
            raise ValueError("Frozen artifact changed")
    gold = json.loads(Path(manifest["gold_snapshot"]).read_text(encoding="utf-8"))
    if digest(Path(manifest["gold_snapshot"])) != manifest["gold_sha256"]:
        raise ValueError("Gold hash mismatch")
    topic = next(t for t in gold["topics"] if t["id"] == manifest["topic"])
    gold_nodes, gold_relations = validate_gold_topic(topic)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (review.get("gold_sha256") != manifest["gold_sha256"] or
            not review.get("reviewer") or set(review.get("arms", {})) != set(ARMS)):
        raise ValueError("Review identity or arms mismatch")
    chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    result = {"topic": topic["id"], "reviewer": review["reviewer"], "review_status": "assistant preliminary explicit review; not human double annotation",
              "gold_sha256": manifest["gold_sha256"], "review_sha256": digest(review_path),
              "scorer_sha256": digest(Path(__file__)), "arms": {}}
    for name in ARMS:
        path = directory / name / "graph.json"
        decisions = review["arms"][name]
        for folder in ("agents", "prompts"):
            for frozen in (directory / "snapshot" / folder).glob("*"):
                if frozen.is_file() and digest(frozen) != digest(directory / name / "runtime" / folder / frozen.name):
                    raise ValueError("Runtime source or prompt differs from frozen artifact")
        if digest(path) != executions[name]["graph_sha256"] or digest(path) != decisions.get("graph_sha256"):
            raise ValueError("Graph hash mismatch")
        graph = json.loads(path.read_text(encoding="utf-8"))
        validate_graph(graph, manifest, executions[name], chunks)
        validate_requests(directory, name, chunks)
        validate_request_audit(directory, name, graph)
        reviewed, alignments = validate_review_arm(decisions, graph, gold_nodes)
        required = [n for n in gold_nodes.values() if n["requirement"] == "M"]
        coverage = {}
        for tier in ("core", "detail"):
            ids = {n["gold_id"] for n in required if n["tier"] == tier}
            covered = {gid for gid in ids if reviewed["coverage"][gid]["covered"]}
            complete = {gid for gid in covered if reviewed["coverage"][gid]["description_complete"]}
            coverage[tier] = {"covered": len(covered), "total": len(ids), "complete_descriptions": len(complete),
                              "missing_gold_ids": sorted(ids - covered)}
        accepted = {r["id"] for r in decisions["relations"] if r["verdict"] == "valid"}
        edges = {(alignments.get(r["source_id"]), r["type"], alignments.get(r["target_id"]))
                 for r in graph["relations"] if r["id"] in accepted}
        required_relations = [r for r in gold_relations if r["requirement"] == "M"]
        hits = {r["gold_id"] for r in required_relations
                if (r["source_gold_id"], r["type"], r["target_gold_id"]) in edges}
        endpoints = {n for r in graph["relations"] for n in (r["source_id"], r["target_id"])}
        result["arms"][name] = {
            "nodes": len(graph["nodes"]), "relations": len(graph["relations"]),
            "required_content_coverage": coverage,
            "exact_entity_hits": _tier_counts(required, set(alignments.values())),
            "strict_M_relation_hits": _tier_counts(required_relations, hits),
            "node_verdicts": {v: sum(n["verdict"] == v for n in decisions["nodes"])
                              for v in ("valid", "modeling_issue", "uncertain")},
            "relation_verdicts": {v: sum(n["verdict"] == v for n in decisions["relations"])
                                  for v in ("valid", "modeling_issue", "uncertain")},
            "duplicate_extra_nodes": sum(len(g) - 1 for g in reviewed["duplicate_groups"]),
            "false_merge_count": len(reviewed["false_merge_ids"]),
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
