"""Score explicit review annotations for a frozen cross-topic memory run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from run_memory_ablation import validate_graph
from score_memory_ablation import action_audit


ARMS = ("visible_memory", "no_visible_memory")
TIERS = ("core", "detail")
ALL_TIERS = frozenset(("core", "detail", "support"))
REQUIREMENTS = frozenset(("M", "O"))
VERDICTS = frozenset(("valid", "modeling_issue", "uncertain"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_path(run_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_decisions(decisions: list, items: list, label: str) -> None:
    if not isinstance(decisions, list) or not isinstance(items, list):
        raise ValueError(f"{label} review decisions and predictions must be arrays")
    prediction_ids = [item.get("id") for item in items if isinstance(item, dict)]
    decision_ids = [row.get("id") for row in decisions if isinstance(row, dict)]
    if (len(prediction_ids) != len(items) or any(not _nonempty_string(item_id) for item_id in prediction_ids)
            or len(set(prediction_ids)) != len(prediction_ids)
            or len(decision_ids) != len(decisions)
            or any(not _nonempty_string(item_id) for item_id in decision_ids)
            or len(set(decision_ids)) != len(decision_ids)
            or set(decision_ids) != set(prediction_ids)):
        raise ValueError(f"Review must cover every {label} prediction exactly once")
    if any(not isinstance(row.get("verdict"), str) or row["verdict"] not in VERDICTS
           or not _nonempty_string(row.get("reason"))
           for row in decisions):
        raise ValueError(f"Every {label} prediction needs an explicit verdict and reason")


def validate_gold_topic(topic: dict) -> tuple[dict[str, dict], list[dict]]:
    nodes = topic.get("nodes")
    relations = topic.get("relations")
    if not isinstance(nodes, list) or not isinstance(relations, list):
        raise ValueError("Gold topic must contain node and relation arrays")

    node_by_id: dict[str, dict] = {}
    for row in nodes:
        if not isinstance(row, dict):
            raise ValueError("Gold node entries must be objects")
        gold_id = row.get("gold_id")
        if (not _nonempty_string(gold_id) or gold_id in node_by_id
                or not _nonempty_string(row.get("type"))
                or not isinstance(row.get("requirement"), str)
                or row["requirement"] not in REQUIREMENTS
                or not isinstance(row.get("tier"), str)
                or row["tier"] not in ALL_TIERS):
            raise ValueError(f"Invalid or duplicate Gold node metadata: {gold_id!r}")
        node_by_id[gold_id] = row

    relation_ids: set[str] = set()
    for row in relations:
        if not isinstance(row, dict):
            raise ValueError("Gold relation entries must be objects")
        gold_id = row.get("gold_id")
        if (not _nonempty_string(gold_id) or gold_id in relation_ids
                or not _nonempty_string(row.get("source_gold_id"))
                or row["source_gold_id"] not in node_by_id
                or not _nonempty_string(row.get("target_gold_id"))
                or row["target_gold_id"] not in node_by_id
                or not _nonempty_string(row.get("type"))
                or not isinstance(row.get("requirement"), str)
                or row["requirement"] not in REQUIREMENTS
                or not isinstance(row.get("tier"), str)
                or row["tier"] not in ALL_TIERS):
            raise ValueError(f"Invalid or duplicate Gold relation metadata: {gold_id!r}")
        relation_ids.add(gold_id)

    if any(row["requirement"] == "M" and row["tier"] not in TIERS for row in nodes):
        raise ValueError("Required Gold nodes must use core or detail tier")
    if any(row["requirement"] == "M" and row["tier"] not in TIERS for row in relations):
        raise ValueError("Required Gold relations must use core or detail tier")
    return node_by_id, relations


def validate_coverage(decisions: list, gold_nodes: dict[str, dict], prediction_nodes: dict[str, dict]) -> dict[str, dict]:
    if not isinstance(decisions, list):
        raise ValueError("Coverage decisions must be an array")
    by_gold: dict[str, dict] = {}
    for row in decisions:
        if not isinstance(row, dict):
            raise ValueError("Coverage decisions must be objects")
        gold_id = row.get("gold_id")
        if not _nonempty_string(gold_id) or gold_id not in gold_nodes:
            raise ValueError(f"Coverage references unknown Gold node: {gold_id!r}")
        if gold_id in by_gold:
            raise ValueError(f"Duplicate coverage decision for Gold node: {gold_id}")
        if not _nonempty_string(row.get("reason")):
            raise ValueError(f"Coverage decision needs an explicit reason: {gold_id}")
        if type(row.get("covered")) is not bool or type(row.get("description_complete")) is not bool:
            raise ValueError(f"Coverage flags must be booleans: {gold_id}")
        prediction_ids = row.get("prediction_ids")
        if (not isinstance(prediction_ids, list)
                or any(not _nonempty_string(node_id) for node_id in prediction_ids)
                or len(prediction_ids) != len(set(prediction_ids))):
            raise ValueError(f"Invalid prediction_ids in coverage decision: {gold_id}")
        if row["covered"] and not prediction_ids:
            raise ValueError(f"Covered Gold node needs at least one prediction ID: {gold_id}")
        if not row["covered"] and prediction_ids:
            raise ValueError(f"Uncovered Gold node must have no prediction IDs: {gold_id}")
        if row["description_complete"] and not row["covered"]:
            raise ValueError(f"An uncovered Gold node cannot have a complete description: {gold_id}")
        for node_id in prediction_ids:
            prediction = prediction_nodes.get(node_id)
            if prediction is None:
                raise ValueError(f"Coverage references unknown prediction node: {node_id}")
            if prediction.get("type") != gold_nodes[gold_id]["type"]:
                raise ValueError(f"Coverage prediction type mismatch for Gold node: {gold_id}")
        by_gold[gold_id] = row

    required_ids = {gold_id for gold_id, row in gold_nodes.items() if row["requirement"] == "M"}
    missing = required_ids - set(by_gold)
    if missing:
        raise ValueError(f"Missing required Gold coverage decisions: {', '.join(sorted(missing))}")
    return by_gold


def validate_alignments(decisions: list, gold_nodes: dict[str, dict], prediction_nodes: dict[str, dict]) -> dict[str, str]:
    alignments: dict[str, str] = {}
    used_gold: set[str] = set()
    for row in decisions:
        if "gold_id" not in row:
            continue
        gold_id = row["gold_id"]
        if not _nonempty_string(gold_id) or gold_id not in gold_nodes:
            raise ValueError(f"Alignment references unknown Gold node: {gold_id!r}")
        node_id = row["id"]
        if gold_id in used_gold:
            raise ValueError("Alignment must be one-to-one")
        if row["verdict"] != "valid":
            raise ValueError("Aligned predictions must have a valid verdict")
        if prediction_nodes[node_id].get("type") != gold_nodes[gold_id]["type"]:
            raise ValueError(f"Alignment type mismatch for Gold node: {gold_id}")
        used_gold.add(gold_id)
        alignments[node_id] = gold_id
    return alignments


def validate_review_structure(review: dict) -> None:
    if not isinstance(review, dict) or not _nonempty_string(review.get("reviewer")):
        raise ValueError("Review must name a reviewer")
    if not _nonempty_string(review.get("gold_sha256")):
        raise ValueError("Review must include gold_sha256")
    arms = review.get("arms")
    if not isinstance(arms, dict) or set(arms) != set(ARMS):
        raise ValueError("Review must contain exactly the two memory-ablation arms")


def validate_review_arm(arm_review: dict, graph: dict, gold_nodes: dict[str, dict]) -> tuple[dict, dict[str, str]]:
    if not isinstance(arm_review, dict):
        raise ValueError("Review arm must be an object")
    nodes = graph.get("nodes")
    relations = graph.get("relations")
    if not isinstance(nodes, list) or not isinstance(relations, list):
        raise ValueError("Prediction graph must contain node and relation arrays")
    validate_decisions(arm_review.get("nodes"), nodes, "node")
    validate_decisions(arm_review.get("relations"), relations, "relation")
    prediction_nodes = {row["id"]: row for row in nodes}
    coverage = validate_coverage(arm_review.get("coverage"), gold_nodes, prediction_nodes)
    alignments = validate_alignments(arm_review["nodes"], gold_nodes, prediction_nodes)

    duplicate_groups = arm_review.get("duplicate_groups")
    if not isinstance(duplicate_groups, list):
        raise ValueError("duplicate_groups must be an array")
    duplicate_ids = []
    for group in duplicate_groups:
        if (not isinstance(group, list) or len(group) < 2
                or any(not _nonempty_string(node_id) for node_id in group)):
            raise ValueError("Invalid duplicate group")
        duplicate_ids.extend(group)
    if len(duplicate_ids) != len(set(duplicate_ids)) or not set(duplicate_ids) <= set(prediction_nodes):
        raise ValueError("Invalid or overlapping duplicate groups")

    false_merge_ids = arm_review.get("false_merge_ids")
    if (not isinstance(false_merge_ids, list)
            or any(not _nonempty_string(node_id) for node_id in false_merge_ids)
            or len(false_merge_ids) != len(set(false_merge_ids))
            or not set(false_merge_ids) <= set(prediction_nodes)):
        raise ValueError("Invalid false_merge_ids")
    if not isinstance(arm_review.get("field_issues"), list):
        raise ValueError("field_issues must be an array")

    for relation in relations:
        if (not _nonempty_string(relation.get("type"))
                or not _nonempty_string(relation.get("source_id"))
                or relation["source_id"] not in prediction_nodes
                or not _nonempty_string(relation.get("target_id"))
                or relation["target_id"] not in prediction_nodes):
            raise ValueError(f"Invalid predicted relation endpoints or type: {relation.get('id')!r}")
    return {"coverage": coverage, "duplicate_groups": duplicate_groups,
            "false_merge_ids": false_merge_ids}, alignments


def _tier_counts(gold_rows: list[dict], ids: set[str]) -> dict[str, dict]:
    result = {}
    for tier in TIERS:
        tier_ids = {row["gold_id"] for row in gold_rows if row["tier"] == tier}
        hits = tier_ids & ids
        result[tier] = {"hits": len(hits), "total": len(tier_ids),
                        "hit_gold_ids": sorted(hits), "missing_gold_ids": sorted(tier_ids - hits)}
    return result


def score(run_dir: Path, review_path: Path) -> dict:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "manifest.json")
    if manifest.get("experiment") != "cross_topic_model_visible_memory":
        raise ValueError("Run manifest is not a cross-topic memory-ablation run")
    if not _nonempty_string(manifest.get("topic")):
        raise ValueError("Run manifest must identify its Gold topic")
    executions = manifest.get("arms")
    if (not isinstance(executions, list) or len(executions) != len(ARMS)
            or any(not isinstance(arm, dict) for arm in executions)
            or {arm.get("name") for arm in executions} != set(ARMS)):
        raise ValueError("Run manifest must contain exactly the two memory-ablation arms")
    if any(arm.get("status") != "completed" for arm in executions):
        raise ValueError("Both run arms must be completed before scoring")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Run manifest must list frozen artifacts")
    artifact_paths = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not _nonempty_string(artifact.get("snapshot")):
            raise ValueError("Malformed frozen artifact entry")
        artifact_path = run_path(run_dir, artifact["snapshot"]).resolve()
        artifact_paths.add(artifact_path)
        if not artifact_path.is_file() or sha256(artifact_path) != artifact.get("sha256"):
            raise ValueError(f"Frozen artifact changed before scoring: {artifact_path}")

    gold_path = run_path(run_dir, manifest.get("gold_snapshot", "")).resolve()
    if gold_path not in artifact_paths or not gold_path.is_file():
        raise ValueError("Frozen Gold snapshot must be listed as a run artifact")
    gold_hash = sha256(gold_path)
    if gold_hash != manifest.get("gold_sha256"):
        raise ValueError("Manifest Gold hash does not match its frozen snapshot")

    review = read_json(review_path)
    validate_review_structure(review)
    if review["gold_sha256"] != gold_hash:
        raise ValueError("Review Gold hash mismatch")

    gold_doc = read_json(gold_path)
    topics = gold_doc.get("topics")
    if not isinstance(topics, list):
        raise ValueError("Frozen Gold must contain a topics array")
    matching_topics = [topic for topic in topics
                       if isinstance(topic, dict) and topic.get("id") == manifest["topic"]]
    if len(matching_topics) != 1:
        raise ValueError("Run topic must match exactly one frozen Gold topic")
    gold_nodes, gold_relations = validate_gold_topic(matching_topics[0])

    chunks_path = run_dir / "chunks.json"
    if chunks_path.resolve() not in artifact_paths:
        raise ValueError("Frozen chunk sequence must be listed as a run artifact")
    chunks = read_json(chunks_path)
    if not isinstance(chunks, list) or len(chunks) != manifest.get("chunk_count"):
        raise ValueError("Frozen chunk sequence does not match manifest chunk_count")

    result = {
        "review_status": "explicit reviewer annotations; no automatic semantic matching or human-annotation claim",
        "reviewer": review["reviewer"],
        "topic": manifest["topic"],
        "review_sha256": sha256(review_path),
        "gold_sha256": gold_hash,
        "validator_sha256": sha256(Path(__file__).with_name("run_memory_ablation.py")),
        "arms": {},
    }
    execution_by_name = {arm["name"]: arm for arm in executions}
    for name in ARMS:
        execution = execution_by_name[name]
        graph_path = run_dir / name / "graph.json"
        graph_hash = sha256(graph_path) if graph_path.is_file() else None
        if graph_hash is None or graph_hash != execution.get("graph_sha256"):
            raise ValueError(f"Manifest graph hash mismatch: {name}")
        arm_review = review["arms"][name]
        if not isinstance(arm_review, dict) or arm_review.get("graph_sha256") != graph_hash:
            raise ValueError(f"Review graph hash mismatch: {name}")
        graph = read_json(graph_path)
        validate_graph(graph, manifest, execution, chunks)
        review_data, alignments = validate_review_arm(arm_review, graph, gold_nodes)

        node_decisions = {row["id"]: row for row in arm_review["nodes"]}
        relation_decisions = {row["id"]: row for row in arm_review["relations"]}
        required_nodes = [row for row in gold_nodes.values() if row["requirement"] == "M"]
        optional_nodes = [row for row in gold_nodes.values() if row["requirement"] == "O"]
        coverage = review_data["coverage"]
        covered_gold = {gold_id for gold_id, row in coverage.items() if row["covered"]}
        complete_gold = {gold_id for gold_id, row in coverage.items()
                         if row["covered"] and row["description_complete"]}
        required_coverage = {}
        for tier in TIERS:
            tier_rows = [row for row in required_nodes if row["tier"] == tier]
            tier_ids = {row["gold_id"] for row in tier_rows}
            covered_ids = tier_ids & covered_gold
            complete_ids = tier_ids & complete_gold
            required_coverage[tier] = {
                "covered": len(covered_ids),
                "total": len(tier_ids),
                "covered_gold_ids": sorted(covered_ids),
                "missing_gold_ids": sorted(tier_ids - covered_ids),
                "complete_descriptions": len(complete_ids),
                "incomplete_description_gold_ids": sorted(covered_ids - complete_ids),
            }

        aligned_gold = set(alignments.values())
        exact_entity_hits = _tier_counts(required_nodes, aligned_gold)
        optional_hits = {row["gold_id"] for row in optional_nodes} & aligned_gold
        accepted_relations = {relation_id for relation_id, row in relation_decisions.items()
                              if row["verdict"] == "valid"}
        graph_relations = {row["id"]: row for row in graph["relations"]}
        predicted_edges = {
            (alignments.get(row["source_id"]), row["type"], alignments.get(row["target_id"]))
            for relation_id, row in graph_relations.items() if relation_id in accepted_relations
        }
        required_relations = [row for row in gold_relations if row["requirement"] == "M"]
        relation_hits = {
            row["gold_id"] for row in required_relations
            if (row["source_gold_id"], row["type"], row["target_gold_id"]) in predicted_edges
        }
        strict_relation_counts = _tier_counts(required_relations, relation_hits)

        diagnostics = graph["diagnostics"]
        summaries = diagnostics["chunk_summaries"]
        endpoint_ids = {endpoint for row in graph["relations"]
                        for endpoint in (row["source_id"], row["target_id"])}
        result["arms"][name] = {
            "offline_configuration_gate": "passed",
            "original_runner_status": execution["status"],
            "nodes": len(graph["nodes"]),
            "relations": len(graph["relations"]),
            "required_content_coverage": required_coverage,
            "exact_entity_hits": exact_entity_hits,
            "optional_node_hits": {"hits": len(optional_hits), "hit_gold_ids": sorted(optional_hits)},
            "strict_M_relation_hits": strict_relation_counts,
            "valid_node_count": sum(row["verdict"] == "valid" for row in arm_review["nodes"]),
            "node_modeling_issue_count": sum(row["verdict"] == "modeling_issue" for row in arm_review["nodes"]),
            "uncertain_node_count": sum(row["verdict"] == "uncertain" for row in arm_review["nodes"]),
            "valid_relation_count": len(accepted_relations),
            "relation_modeling_issue_count": sum(row["verdict"] == "modeling_issue" for row in arm_review["relations"]),
            "uncertain_relation_count": sum(row["verdict"] == "uncertain" for row in arm_review["relations"]),
            "duplicate_extra_nodes": sum(len(group) - 1 for group in review_data["duplicate_groups"]),
            "false_merge_count": len(review_data["false_merge_ids"]),
            "isolated_node_count": len({row["id"] for row in graph["nodes"]} - endpoint_ids),
            "chunk_memory_nodes": [row["memory_node_count"] for row in summaries],
            "chunk_memory_relations": [row["memory_relation_count"] for row in summaries],
            "api_summary": diagnostics["api_summary"],
            "merge_audit": diagnostics.get("audit"),
            "action_audit": action_audit(run_dir, name, chunks, summaries),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.run_dir, args.review)
    output = args.run_dir / "reviewed_metrics.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
