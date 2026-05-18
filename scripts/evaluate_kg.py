from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HIERARCHY_LEVEL = {
    "Chapter": 0,
    "Section": 1,
    "Topic": 2,
    "Subtopic": 3,
}

SEMANTIC_RELATION_TYPES = {
    "has_definition",
    "has_formula",
    "has_property",
    "requires",
    "has_condition",
    "derived_from",
    "equivalent_to",
    "applies_to",
    "uses_method",
    "involves_concept",
    "example_of",
    "proved_by",
    "illustrated_by",
}


def main() -> int:
    args = parse_args()
    path = resolve_input(args.input, args.kg_dir)
    data = load_json(path)
    report = evaluate(data, path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report, args.samples)
    if args.fail_on_severe and report["summary"]["severe_issue_count"] > 0:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a MaF-KG agent v3 JSON output.")
    parser.add_argument(
        "input",
        nargs="?",
        help="KG JSON file. If omitted, the latest *_concept_formula_kg_agent_v3.json under --kg-dir is used.",
    )
    parser.add_argument("--kg-dir", default="KG", help="Directory used when input is omitted.")
    parser.add_argument("--json", action="store_true", help="Print full machine-readable JSON report.")
    parser.add_argument("--samples", type=int, default=12, help="Number of sample issues to print per category.")
    parser.add_argument(
        "--fail-on-severe",
        action="store_true",
        help="Exit with code 1 when severe structural/schema issues are found.",
    )
    return parser.parse_args()


def resolve_input(input_arg: str | None, kg_dir: str) -> Path:
    if input_arg:
        path = Path(input_arg)
        if not path.exists():
            raise SystemExit(f"Input file not found: {path}")
        return path
    candidates = sorted(
        Path(kg_dir).glob("*_concept_formula_kg_agent_v3.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No *_concept_formula_kg_agent_v3.json files found under {kg_dir}")
    return candidates[0]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def evaluate(data: dict[str, Any], path: Path) -> dict[str, Any]:
    nodes = data.get("nodes", [])
    relationships = data.get("relationships", [])
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    node_type_by_id = {node_id: node.get("type") for node_id, node in node_by_id.items()}

    invalid_refs = [
        rel
        for rel in relationships
        if rel.get("source") not in node_by_id or rel.get("target") not in node_by_id
    ]
    self_loops = [rel for rel in relationships if rel.get("source") == rel.get("target")]
    ids_with_edges = {rel.get("source") for rel in relationships} | {rel.get("target") for rel in relationships}
    isolated_nodes = [node for node in nodes if node.get("id") not in ids_with_edges]

    contains = [rel for rel in relationships if rel.get("type") == "contains"]
    bad_contains = [rel for rel in contains if not contains_is_adjacent_hierarchy(rel, node_type_by_id)]
    bad_directions = [
        rel for rel in relationships if not relationship_direction_allowed(rel, node_type_by_id, bad_contains)
    ]

    formula_nodes = [node for node in nodes if node.get("type") == "Formula"]
    formula_without_latex = [node for node in formula_nodes if not node.get("content", {}).get("latex")]
    top_level_latex_nodes = [node for node in nodes if "latex" in node]
    top_level_confidence_nodes = [node for node in nodes if "confidence" in node]
    top_level_confidence_relationships = [rel for rel in relationships if "confidence" in rel]
    content_text_nodes = [node for node in nodes if "text" in node.get("content", {})]
    missing_description_nodes = [
        node
        for node in nodes
        if node.get("type") not in {"Formula", "Figure"} and not str(node.get("description", "")).strip()
    ]
    weak_description_nodes = [
        node
        for node in nodes
        if node.get("type") in {"Definition", "Theorem", "Property", "Example", "Method", "Condition"}
        and len(str(node.get("description", "")).strip()) < 12
    ]

    relationship_no_evidence = [rel for rel in relationships if not rel.get("evidence")]
    relationship_no_reason = [
        rel
        for rel in relationships
        if rel.get("type") not in {"belongs_to", "contains"} and not rel.get("content", {}).get("reason")
    ]
    low_quality_relationships = [
        rel for rel in relationships if relationship_confidence(rel) < 0.55
    ]

    figure_nodes = [node for node in nodes if node.get("type") == "Figure"]
    illustrated_targets = {rel.get("target") for rel in relationships if rel.get("type") == "illustrated_by"}
    figures_without_illustrated_by = [
        node for node in figure_nodes if node.get("id") not in illustrated_targets
    ]

    semantic_relationships = [
        rel for rel in relationships if rel.get("type") in SEMANTIC_RELATION_TYPES
    ]
    semantic_node_ids = {rel.get("source") for rel in semantic_relationships} | {
        rel.get("target") for rel in semantic_relationships
    }
    only_structural_nodes = [
        node
        for node in nodes
        if node.get("type") not in HIERARCHY_LEVEL and node.get("id") not in semantic_node_ids
    ]

    duplicate_node_ids = duplicate_values([node.get("id") for node in nodes if node.get("id")])
    near_duplicate_groups = detect_near_duplicate_groups(nodes)
    suspicious_pairs = detect_suspicious_pairs(nodes)
    semantic_evidence_mismatches = detect_semantic_evidence_mismatches(
        semantic_relationships, node_by_id
    )
    opposite_example_of_suspects = detect_opposite_example_of_suspects(relationships, node_by_id)

    counts = {
        "node_count": len(nodes),
        "relationship_count": len(relationships),
        "node_count_by_type": dict(Counter(node.get("type") for node in nodes)),
        "relationship_count_by_type": dict(Counter(rel.get("type") for rel in relationships)),
    }
    issues = {
        "invalid_ref": issue_block(invalid_refs, relationship_label, relationships),
        "self_loop": issue_block(self_loops, relationship_label, relationships),
        "isolated_node": issue_block(isolated_nodes, node_label, nodes),
        "bad_direction": issue_block(bad_directions, relationship_label, relationships),
        "bad_contains": issue_block(bad_contains, relationship_label, relationships),
        "formula_without_latex": issue_block(formula_without_latex, node_label, nodes),
        "top_level_latex_node": issue_block(top_level_latex_nodes, node_label, nodes),
        "top_level_confidence_node": issue_block(top_level_confidence_nodes, node_label, nodes),
        "top_level_confidence_relationship": issue_block(
            top_level_confidence_relationships, relationship_label, relationships
        ),
        "content_text_node": issue_block(content_text_nodes, node_label, nodes),
        "missing_description_node": issue_block(missing_description_nodes, node_label, nodes),
        "weak_description_node": issue_block(weak_description_nodes, node_label, nodes),
        "relationship_without_evidence": issue_block(
            relationship_no_evidence, relationship_label, relationships
        ),
        "semantic_relationship_without_reason": issue_block(
            relationship_no_reason, relationship_label, relationships
        ),
        "low_quality_relationship": issue_block(
            low_quality_relationships, relationship_label, relationships
        ),
        "figure_without_illustrated_by": issue_block(
            figures_without_illustrated_by, node_label, nodes
        ),
        "only_structural_nonhierarchy_node": issue_block(
            only_structural_nodes, node_label, nodes
        ),
        "duplicate_node_id": {
            "count": len(duplicate_node_ids),
            "samples": [{"id": key, "count": value} for key, value in duplicate_node_ids[:20]],
        },
        "near_duplicate_group": {
            "count": len(near_duplicate_groups),
            "samples": near_duplicate_groups[:20],
        },
        "suspicious_pair": {
            "count": len(suspicious_pairs),
            "samples": suspicious_pairs[:20],
        },
        "semantic_evidence_mismatch": {
            "count": len(semantic_evidence_mismatches),
            "samples": semantic_evidence_mismatches[:20],
        },
        "opposite_example_of_suspect": {
            "count": len(opposite_example_of_suspects),
            "samples": opposite_example_of_suspects[:20],
        },
    }
    severe_issue_keys = [
        "invalid_ref",
        "self_loop",
        "bad_direction",
        "bad_contains",
        "formula_without_latex",
        "top_level_latex_node",
        "top_level_confidence_node",
        "top_level_confidence_relationship",
        "content_text_node",
    ]
    severe_issue_count = sum(issues[key]["count"] for key in severe_issue_keys)

    return {
        "file": str(path),
        "schema_version": data.get("schema_version"),
        "source_path": data.get("source_path"),
        "title": data.get("title"),
        "model": data.get("model"),
        "counts": counts,
        "diagnostics": data.get("diagnostics", {}),
        "issues": issues,
        "summary": {
            "severe_issue_count": severe_issue_count,
            "warning_issue_count": sum(block["count"] for block in issues.values()) - severe_issue_count,
            "is_structurally_clean": severe_issue_count == 0,
        },
    }


def contains_is_adjacent_hierarchy(rel: dict[str, Any], node_type_by_id: dict[str, str]) -> bool:
    source_type = node_type_by_id.get(rel.get("source"))
    target_type = node_type_by_id.get(rel.get("target"))
    if source_type not in HIERARCHY_LEVEL or target_type not in HIERARCHY_LEVEL:
        return False
    return HIERARCHY_LEVEL[target_type] == HIERARCHY_LEVEL[source_type] + 1


def relationship_direction_allowed(
    rel: dict[str, Any],
    node_type_by_id: dict[str, str],
    bad_contains: list[dict[str, Any]],
) -> bool:
    rel_type = rel.get("type")
    source_type = node_type_by_id.get(rel.get("source"))
    target_type = node_type_by_id.get(rel.get("target"))
    if not source_type or not target_type:
        return False
    if rel_type == "belongs_to":
        return target_type in HIERARCHY_LEVEL
    if rel_type == "contains":
        return rel not in bad_contains
    if rel_type == "has_definition":
        return source_type == "Concept" and target_type == "Definition"
    if rel_type == "has_formula":
        return target_type == "Formula" and source_type in {
            "Concept",
            "Definition",
            "Theorem",
            "Property",
            "Method",
            "Example",
        }
    if rel_type == "has_property":
        return target_type == "Property" and source_type in {
            "Concept",
            "Definition",
            "Formula",
            "Theorem",
            "Example",
        }
    if rel_type == "has_condition":
        return target_type == "Condition" and source_type in {
            "Concept",
            "Definition",
            "Formula",
            "Theorem",
            "Property",
            "Method",
            "Example",
        }
    if rel_type == "requires":
        return target_type == "Condition" and source_type in {
            "Concept",
            "Formula",
            "Theorem",
            "Property",
            "Method",
            "Example",
        }
    if rel_type in {"derived_from", "equivalent_to"}:
        return source_type == "Formula" and target_type == "Formula"
    if rel_type == "applies_to":
        return source_type in {"Formula", "Theorem", "Method"} and target_type in {
            "Concept",
            "Condition",
            "Example",
        }
    if rel_type == "uses_method":
        return source_type == "Example" and target_type == "Method"
    if rel_type == "involves_concept":
        return source_type in {
            "Example",
            "Formula",
            "Theorem",
            "Property",
            "Method",
            "Definition",
            "Proof",
            "Figure",
        } and target_type == "Concept"
    if rel_type == "example_of":
        return source_type == "Example" and target_type in {
            "Concept",
            "Definition",
            "Formula",
            "Theorem",
            "Property",
            "Method",
        }
    if rel_type == "proved_by":
        return target_type == "Proof" and source_type in {"Formula", "Theorem", "Property", "Definition"}
    if rel_type == "illustrated_by":
        return target_type == "Figure" and source_type in {
            "Concept",
            "Definition",
            "Formula",
            "Theorem",
            "Property",
            "Example",
        }
    return False


def relationship_confidence(rel: dict[str, Any]) -> float:
    value = rel.get("quality", {}).get("confidence", 0.75)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def duplicate_values(values: list[str]) -> list[tuple[str, int]]:
    counts = Counter(values)
    return sorted(
        [(key, count) for key, count in counts.items() if count > 1],
        key=lambda item: (-item[1], item[0]),
    )


def normalized_name(name: str) -> str:
    text = re.sub(r"\s+", "", str(name))
    for char in "：:、.-（）()[]【】":
        text = text.replace(char, "")
    return text.replace("的", "")


def detect_near_duplicate_groups(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        node_type = node.get("type")
        if node_type in HIERARCHY_LEVEL:
            continue
        groups[(node_type, normalized_name(node.get("name", "")))].append(node)
    result = []
    for (node_type, key), items in groups.items():
        if len(items) <= 1:
            continue
        result.append(
            {
                "type": node_type,
                "normalized_name": key,
                "nodes": [node_label(node) for node in items],
            }
        )
    return sorted(result, key=lambda item: (item["type"], item["normalized_name"]))


def detect_suspicious_pairs(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    pairs = []
    for index, left in enumerate(nodes):
        left_type = left.get("type")
        if left_type in HIERARCHY_LEVEL:
            continue
        left_name = normalized_name(left.get("name", ""))
        for right in nodes[index + 1 :]:
            if right.get("type") != left_type or right.get("type") in HIERARCHY_LEVEL:
                continue
            right_name = normalized_name(right.get("name", ""))
            if len(left_name) < 4 or len(right_name) < 4:
                continue
            if left_name in right_name or right_name in left_name:
                pairs.append(
                    {
                        "type": left_type,
                        "left": node_label(left),
                        "right": node_label(right),
                    }
                )
    return pairs


def detect_semantic_evidence_mismatches(
    semantic_relationships: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    mismatches = []
    for rel in semantic_relationships:
        evidence = (rel.get("evidence") or [{}])[0]
        line = evidence.get("start_line")
        if not isinstance(line, int):
            continue
        source = node_by_id.get(rel.get("source"))
        target = node_by_id.get(rel.get("target"))
        if not source or not target:
            continue
        if not evidence_near_node(line, source) and not evidence_near_node(line, target):
            mismatches.append(
                {
                    "relationship": relationship_label(rel),
                    "evidence_line": line,
                    "reason": rel.get("content", {}).get("reason", ""),
                }
            )
    return mismatches


def evidence_near_node(line: int, node: dict[str, Any]) -> bool:
    for evidence in node.get("evidence", [])[:3]:
        start = evidence.get("start_line", 0)
        end = evidence.get("end_line", 0)
        if start - 3 <= line <= end + 3:
            return True
    return False


def detect_opposite_example_of_suspects(
    relationships: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    suspects = []
    pairs = [
        ("无界", "有界"),
        ("发散", "收敛"),
        ("不收敛", "收敛"),
    ]
    for rel in relationships:
        if rel.get("type") != "example_of":
            continue
        source = node_by_id.get(rel.get("source"))
        target = node_by_id.get(rel.get("target"))
        if not source or not target:
            continue
        if target.get("type") not in {"Concept", "Definition"}:
            continue
        source_text = f"{source.get('name', '')} {source.get('description', '')}"
        target_name = str(target.get("name", ""))
        target_text = f"{target.get('name', '')} {target.get('description', '')}"
        for negative, positive in pairs:
            if negative in source_text and target_name.startswith(positive) and negative not in target_text:
                suspects.append(
                    {
                        "relationship": relationship_label(rel),
                        "source": node_label(source),
                        "target": node_label(target),
                        "reason": rel.get("content", {}).get("reason", ""),
                    }
                )
    return suspects


def issue_block(
    items: list[dict[str, Any]],
    label_fn,
    all_items: list[dict[str, Any]],
    sample_size: int = 20,
) -> dict[str, Any]:
    del all_items
    return {
        "count": len(items),
        "samples": [label_fn(item) for item in items[:sample_size]],
    }


def node_label(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "name": node.get("name"),
        "description": str(node.get("description", ""))[:120],
    }


def relationship_label(rel: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rel.get("id"),
        "type": rel.get("type"),
        "source": rel.get("source"),
        "target": rel.get("target"),
        "reason": str(rel.get("content", {}).get("reason", ""))[:160],
    }


def print_text_report(report: dict[str, Any], sample_size: int) -> None:
    counts = report["counts"]
    issues = report["issues"]
    summary = report["summary"]
    print(f"File: {report['file']}")
    print(f"Schema: {report.get('schema_version')}")
    print(f"Source: {report.get('source_path')}")
    print(f"Nodes: {counts['node_count']}")
    print(f"Relationships: {counts['relationship_count']}")
    print(f"Severe issues: {summary['severe_issue_count']}")
    print(f"Warning issues: {summary['warning_issue_count']}")
    print(f"Structurally clean: {summary['is_structurally_clean']}")
    print()
    print("Node types:")
    for key, value in sorted(counts["node_count_by_type"].items()):
        print(f"  {key}: {value}")
    print()
    print("Relationship types:")
    for key, value in sorted(counts["relationship_count_by_type"].items()):
        print(f"  {key}: {value}")
    print()
    print("Issue counts:")
    for key, block in issues.items():
        if block["count"]:
            print(f"  {key}: {block['count']}")
    print()
    for key, block in issues.items():
        if not block["count"]:
            continue
        print(f"[{key}] samples")
        for sample in block["samples"][:sample_size]:
            print("  " + json.dumps(sample, ensure_ascii=False))
        print()


if __name__ == "__main__":
    raise SystemExit(main())
