from __future__ import annotations

import argparse
import difflib
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

SEMANTIC_NODE_TYPES = {"Concept", "Claim", "Method"}
SEMANTIC_CLAIM_KINDS = {"property", "criterion", "identity", "result"}
SEMANTIC_RELATION_SIGNATURES: dict[str, set[tuple[str, str]]] = {
    "involves": {("Claim", "Concept")},
    "uses": {("Method", "Concept"), ("Method", "Claim"), ("Method", "Method")},
    "establishes": {("Method", "Claim")},
    "derived_from": {("Claim", "Claim")},
    "equivalent_to": {("Claim", "Claim")},
    "special_case_of": {("Concept", "Concept")},
    "defined_in_terms_of": {("Concept", "Concept")},
}
SEMANTIC_EXPRESSION_ROLES = {
    "definition_representation": "Concept",
    "claim_representation": "Claim",
    "method_template": "Method",
}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    path = resolve_input(args.input, args.kg_dir)
    data = load_json(path)
    report = evaluate(data, path)
    if args.gold:
        gold_path = Path(args.gold)
        gold_data = load_json(gold_path)
        report["gold_evaluation"] = evaluate_nodes_against_gold(
            data, gold_data, gold_path, semantic_match=args.semantic_match
        )
    if args.additional_inputs:
        if not args.gold:
            raise SystemExit("Relation stability evaluation requires --gold for endpoint alignment")
        graphs = [data, *(load_json(Path(p)) for p in args.additional_inputs)]
        report["relation_stability"] = evaluate_relation_stability(graphs, gold_data, gold_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report, args.samples)
    if args.fail_on_severe and report["summary"]["severe_issue_count"] > 0:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a MaF-KG legacy or semantic KG JSON output.")
    parser.add_argument(
        "input",
        nargs="?",
        help="KG JSON file. If omitted, the latest supported JSON file under --kg-dir is used.",
    )
    parser.add_argument(
        "additional_inputs",
        nargs="*",
        help="Additional KG JSON runs of the same source section for cross-run relation "
        "stability evaluation (requires --gold).",
    )
    parser.add_argument("--kg-dir", default="KG", help="Directory used when input is omitted.")
    parser.add_argument("--json", action="store_true", help="Print full machine-readable JSON report.")
    parser.add_argument(
        "--gold",
        help="Optional reviewed semantic Gold JSON used to calculate node precision, recall, and F1.",
    )
    parser.add_argument("--samples", type=int, default=12, help="Number of sample issues to print per category.")
    parser.add_argument(
        "--fail-on-severe",
        action="store_true",
        help="Exit with code 1 when severe structural/schema issues are found.",
    )
    parser.add_argument(
        "--semantic-match",
        action="store_true",
        help="Fall back to embedding retrieval + LLM judge for gold name matching "
        "when canonical rule matching fails (requires local ollama qwen3-embedding "
        "and the DEEPSEEK_API_KEY environment variable).",
    )
    return parser.parse_args()


def resolve_input(input_arg: str | None, kg_dir: str) -> Path:
    if input_arg:
        path = Path(input_arg)
        if not path.exists():
            raise SystemExit(f"Input file not found: {path}")
        return path
    root = Path(kg_dir)
    candidates = sorted(
        {
            *root.glob("*_concept_formula_kg_agent_v3.json"),
            *root.glob("*_语义图_*.json"),
            *root.glob("*_semantic_*.json"),
        },
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No supported KG JSON files found under {kg_dir}")
    return candidates[0]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def evaluate(data: dict[str, Any], path: Path) -> dict[str, Any]:
    if str(data.get("schema_version", "")).startswith("maf-kg.semantic."):
        return evaluate_semantic_graph(data, path)

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


def evaluate_semantic_graph(data: dict[str, Any], path: Path) -> dict[str, Any]:
    nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []
    relations = data.get("relations", []) if isinstance(data.get("relations"), list) else []
    expressions = data.get("math_expressions", []) if isinstance(data.get("math_expressions"), list) else []
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    node_type_by_id = {node_id: node.get("type") for node_id, node in node_by_id.items()}

    invalid_node_types = [node for node in nodes if node.get("type") not in SEMANTIC_NODE_TYPES]
    missing_descriptions = [node for node in nodes if not str(node.get("description", "")).strip()]
    claim_kind_errors = [
        node
        for node in nodes
        if node.get("type") == "Claim" and node.get("claim_kind") not in SEMANTIC_CLAIM_KINDS
    ]
    unexpected_claim_kinds = [
        node
        for node in nodes
        if node.get("type") != "Claim" and node.get("claim_kind") is not None
    ]
    duplicate_node_ids = duplicate_values([node.get("id") for node in nodes if node.get("id")])
    duplicate_expression_ids = duplicate_values(
        [expression.get("id") for expression in expressions if expression.get("id")]
    )
    canonical_counts = Counter(
        (node.get("type"), canonical_node_name(str(node.get("name", ""))))
        for node in nodes
        if str(node.get("name", "")).strip()
    )
    duplicate_canonical_nodes = [
        {"type": node_type, "name_key": name_key, "count": count}
        for (node_type, name_key), count in sorted(canonical_counts.items())
        if count > 1
    ]

    invalid_refs = [
        relation
        for relation in relations
        if relation.get("source_id") not in node_by_id or relation.get("target_id") not in node_by_id
    ]
    self_loops = [
        relation for relation in relations if relation.get("source_id") == relation.get("target_id")
    ]
    invalid_relation_types = [
        relation for relation in relations if relation.get("type") not in SEMANTIC_RELATION_SIGNATURES
    ]
    invalid_relation_signatures = []
    for relation in relations:
        relation_type = relation.get("type")
        source_type = node_type_by_id.get(relation.get("source_id"))
        target_type = node_type_by_id.get(relation.get("target_id"))
        if relation_type not in SEMANTIC_RELATION_SIGNATURES or source_type is None or target_type is None:
            continue
        if (source_type, target_type) not in SEMANTIC_RELATION_SIGNATURES[relation_type]:
            invalid_relation_signatures.append(relation)
    edge_counts = Counter(
        (relation.get("source_id"), relation.get("type"), relation.get("target_id"))
        for relation in relations
    )
    duplicate_edges = [
        {"source_id": source, "type": relation_type, "target_id": target, "count": count}
        for (source, relation_type, target), count in sorted(edge_counts.items())
        if count > 1
    ]

    expression_owner_errors = [
        expression for expression in expressions if expression.get("owner_id") not in node_by_id
    ]
    expression_role_errors = []
    for expression in expressions:
        expected_type = SEMANTIC_EXPRESSION_ROLES.get(expression.get("role"))
        owner_type = node_type_by_id.get(expression.get("owner_id"))
        if expected_type is None or (owner_type is not None and owner_type != expected_type):
            expression_role_errors.append(expression)
    expression_without_latex = [
        expression for expression in expressions if not str(expression.get("latex", "")).strip()
    ]

    evidence_errors: list[dict[str, Any]] = []
    missing_evidence: list[dict[str, Any]] = []
    for kind, records in (("node", nodes), ("relation", relations), ("expression", expressions)):
        for record in records:
            owner_id = record.get("id") or semantic_record_label(kind, record)
            evidence = record.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                missing_evidence.append({"kind": kind, "record": owner_id})
                continue
            for index, item in enumerate(evidence):
                error = validate_semantic_evidence(item)
                if error:
                    evidence_errors.append(
                        {"kind": kind, "record": owner_id, "evidence_index": index, "error": error}
                    )

    ids_with_edges = {
        endpoint
        for relation in relations
        for endpoint in (relation.get("source_id"), relation.get("target_id"))
        if endpoint in node_by_id
    }
    isolated_nodes = [node for node in nodes if node.get("id") not in ids_with_edges]
    wide_evidence_nodes = []
    for node in nodes:
        spans = [
            item.get("end_line") - item.get("start_line") + 1
            for item in node.get("evidence", [])
            if isinstance(item, dict)
            and isinstance(item.get("start_line"), int)
            and isinstance(item.get("end_line"), int)
        ]
        if spans and max(spans) > 20:
            wide_evidence_nodes.append({"id": node.get("id"), "name": node.get("name"), "max_span": max(spans)})

    component_sizes = semantic_component_sizes(node_by_id, relations)
    chunk_summaries = data.get("diagnostics", {}).get("chunk_summaries", [])
    if not isinstance(chunk_summaries, list):
        chunk_summaries = []
    chunk_merge_failures = [
        {"chunk_id": summary.get("chunk_id"), **summary.get("merge_failure", {})}
        for summary in chunk_summaries
        if isinstance(summary, dict) and isinstance(summary.get("merge_failure"), dict)
    ]
    chunk_identity_conflicts = [
        {"chunk_id": summary.get("chunk_id"), **conflict}
        for summary in chunk_summaries
        if isinstance(summary, dict)
        for conflict in summary.get("identity_conflicts", [])
        if isinstance(conflict, dict)
    ]
    chunk_skipped_items = [
        {"chunk_id": summary.get("chunk_id"), **item}
        for summary in chunk_summaries
        if isinstance(summary, dict)
        for item in summary.get("skipped_items", [])
        if isinstance(item, dict)
    ]
    chunk_pending_unlinked = [
        {"chunk_id": summary.get("chunk_id"), "pending_node_refs": summary.get("pending_node_refs", [])}
        for summary in chunk_summaries
        if isinstance(summary, dict) and summary.get("pending_node_refs")
    ]
    issues = {
        "invalid_node_type": issue_block(invalid_node_types, node_label, nodes),
        "missing_description_node": issue_block(missing_descriptions, node_label, nodes),
        "invalid_claim_kind": issue_block(claim_kind_errors, node_label, nodes),
        "unexpected_claim_kind": issue_block(unexpected_claim_kinds, node_label, nodes),
        "duplicate_node_id": value_issue_block(duplicate_node_ids, "id"),
        "duplicate_canonical_node": direct_issue_block(duplicate_canonical_nodes),
        "invalid_relation_ref": issue_block(invalid_refs, semantic_relation_label, relations),
        "self_loop": issue_block(self_loops, semantic_relation_label, relations),
        "invalid_relation_type": issue_block(invalid_relation_types, semantic_relation_label, relations),
        "invalid_relation_signature": issue_block(
            invalid_relation_signatures, semantic_relation_label, relations
        ),
        "duplicate_edge": direct_issue_block(duplicate_edges),
        "invalid_expression_owner": issue_block(
            expression_owner_errors, semantic_expression_label, expressions
        ),
        "invalid_expression_role": issue_block(
            expression_role_errors, semantic_expression_label, expressions
        ),
        "expression_without_latex": issue_block(
            expression_without_latex, semantic_expression_label, expressions
        ),
        "missing_evidence": direct_issue_block(missing_evidence),
        "invalid_evidence": direct_issue_block(evidence_errors),
        "isolated_node": issue_block(isolated_nodes, node_label, nodes),
        "wide_evidence_node": direct_issue_block(wide_evidence_nodes),
        "chunk_merge_failure": direct_issue_block(chunk_merge_failures),
        "chunk_identity_conflict": direct_issue_block(chunk_identity_conflicts),
        "chunk_skipped_item": direct_issue_block(chunk_skipped_items),
        "chunk_pending_unlinked": direct_issue_block(chunk_pending_unlinked),
    }
    severe_issue_keys = {
        "invalid_node_type",
        "missing_description_node",
        "invalid_claim_kind",
        "unexpected_claim_kind",
        "duplicate_node_id",
        "duplicate_canonical_node",
        "invalid_relation_ref",
        "self_loop",
        "invalid_relation_type",
        "invalid_relation_signature",
        "duplicate_edge",
        "invalid_expression_owner",
        "invalid_expression_role",
        "expression_without_latex",
        "missing_evidence",
        "invalid_evidence",
        "chunk_merge_failure",
        "chunk_identity_conflict",
        "chunk_skipped_item",
    }
    severe_issue_count = sum(issues[key]["count"] for key in severe_issue_keys)
    return {
        "file": str(path),
        "schema_version": data.get("schema_version"),
        "source_path": data.get("source_path"),
        "title": data.get("title"),
        "model": data.get("model"),
        "counts": {
            "node_count": len(nodes),
            "relationship_count": len(relations),
            "math_expression_count": len(expressions),
            "node_count_by_type": dict(Counter(node.get("type") for node in nodes)),
            "claim_count_by_kind": dict(
                Counter(node.get("claim_kind") for node in nodes if node.get("type") == "Claim")
            ),
            "relationship_count_by_type": dict(Counter(rel.get("type") for rel in relations)),
            "expression_count_by_role": dict(Counter(item.get("role") for item in expressions)),
            "connected_component_count": len(component_sizes),
            "connected_component_sizes": component_sizes,
        },
        "diagnostics": data.get("diagnostics", {}),
        "issues": issues,
        "summary": {
            "severe_issue_count": severe_issue_count,
            "warning_issue_count": sum(block["count"] for block in issues.values()) - severe_issue_count,
            "is_structurally_clean": severe_issue_count == 0,
        },
    }


def canonical_node_name(value: str) -> str:
    normalized = re.sub(r"[fg]\(x\)", "", value)
    normalized = (
        normalized.replace("\\to", "→")
        .replace("\\infty", "∞")
        .replace("$", "")
        .replace("{", "")
        .replace("}", "")
    )
    return re.sub(r"[\s_·,，。.;；:：'\"“”‘’()（）\[\]【】{}]+", "", normalized).casefold()


def semantic_record_label(kind: str, record: dict[str, Any]) -> str:
    if kind == "relation":
        return f"{record.get('source_id')} -[{record.get('type')}]-> {record.get('target_id')}"
    if kind == "expression":
        return f"{record.get('owner_id')}:{record.get('role')}:{record.get('id')}"
    return str(record.get("id"))


def validate_semantic_evidence(item: Any) -> str | None:
    if not isinstance(item, dict):
        return "evidence must be an object"
    start = item.get("start_line")
    end = item.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return "invalid line range"
    for key in ("source_path", "text", "chunk_id", "teaching_block_id", "discourse_role"):
        if not str(item.get(key, "")).strip():
            return f"missing {key}"
    if not isinstance(item.get("heading_path"), list):
        return "heading_path must be a list"
    return None


def semantic_component_sizes(
    node_by_id: dict[str, dict[str, Any]], relations: list[dict[str, Any]]
) -> list[int]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for relation in relations:
        source = relation.get("source_id")
        target = relation.get("target_id")
        if source in node_by_id and target in node_by_id:
            adjacency[source].add(target)
            adjacency[target].add(source)
    unseen = set(node_by_id)
    sizes = []
    while unseen:
        stack = [unseen.pop()]
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            for neighbor in adjacency[current] & unseen:
                unseen.remove(neighbor)
                stack.append(neighbor)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def semantic_relation_label(relation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": relation.get("id"),
        "type": relation.get("type"),
        "source_id": relation.get("source_id"),
        "target_id": relation.get("target_id"),
    }


def semantic_expression_label(expression: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": expression.get("id"),
        "owner_id": expression.get("owner_id"),
        "role": expression.get("role"),
        "latex": str(expression.get("latex", ""))[:120],
    }


def direct_issue_block(items: list[dict[str, Any]], sample_size: int = 20) -> dict[str, Any]:
    return {"count": len(items), "samples": items[:sample_size]}


def value_issue_block(items: list[tuple[str, int]], key_name: str) -> dict[str, Any]:
    return {
        "count": len(items),
        "samples": [{key_name: value, "count": count} for value, count in items[:20]],
    }


class SemanticNameMatcher:
    """Fallback gold matching: embedding retrieval selects candidates, an LLM
    judge decides same/different. Used only when canonical rule matching fails,
    so it never overrides deterministic matches; it rescues surface variants
    (word order, inserted f(x), synonym phrasing) that rules cannot handle."""

    EMBED_MODEL = "qwen3-embedding:0.6b"
    JUDGE_MODEL = "deepseek-v4-flash"
    OLLAMA_URL = "http://localhost:11434/api/embed"
    JUDGE_URL = "https://api.deepseek.com/chat/completions"
    SIM_THRESHOLD = 0.80

    def __init__(self) -> None:
        self._mem_embed: dict[str, list[float]] = {}
        self._judge_cache: dict[tuple[str, str], bool] = {}
        self._call_count = 0

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import urllib.request

        req = urllib.request.Request(
            self.OLLAMA_URL,
            data=json.dumps({"model": self.EMBED_MODEL, "input": texts}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())["embeddings"]

    def _judge(self, name_a: str, name_b: str, context: str) -> bool:
        key = (name_a, name_b)
        if key in self._judge_cache:
            return self._judge_cache[key]
        import os
        import urllib.request

        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("DS_API_KEY")
        if not api_key:
            raise SystemExit("--semantic-match requires DEEPSEEK_API_KEY for the judge model")
        sys_prompt = (
            "你是数学知识图谱的命名判定器。给定两个数学概念名，判断它们是否指代"
            "同一个数学对象。同一对象的不同写法（同义改写、措辞顺序不同、含 f(x) "
            "与否、“x→+∞”与“x趋于正无穷”等写法差异）应判定为同一；名称相近但指代"
            "不同对象（如左极限与右极限、概念与其性质、“自变量趋于无穷大”这一趋近"
            "方式与“自变量趋于无穷大时的函数极限”这一极限概念）应判定为不同。"
            '只输出 JSON：{"same": true/false, "reason": "一句话理由"}'
        )
        user_prompt = (
            f'概念A: "{name_a}"；概念B: "{name_b}"。\n'
            f"概念A 的上下文描述：{context}\n判断二者是否同一对象。"
        )
        payload = {
            "model": self.JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            self.JUDGE_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            content = json.loads(resp.read())["choices"][0]["message"]["content"]
        self._call_count += 1
        try:
            verdict = json.loads(content).get("same", False)
        except json.JSONDecodeError:
            verdict = False
        self._judge_cache[key] = verdict
        return verdict

    def cosine(self, a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b)) / (
            (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        )

    def judge_same(self, predicted: dict[str, Any], gold_item: dict[str, Any]) -> bool:
        """Ask the flash judge whether predicted and gold names denote the same object."""
        name_a = str(predicted.get("name", ""))
        name_b = str(gold_item.get("name", ""))
        context = str(predicted.get("description", ""))[:400]
        return self._judge(name_a, name_b, context)

    def best_candidates(self, predicted: dict[str, Any], gold_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank same-type gold items by embedding similarity to the predicted name."""
        if predicted.get("type") not in ("Concept", "Claim", "Method"):
            return []
        query = str(predicted.get("name", ""))
        if not query.strip():
            return []
        unembedded = [item for item in gold_items if item["gold_id"] not in self._mem_embed]
        if unembedded:
            embs = self._embed([item.get("name", "") for item in unembedded])
            for item, emb in zip(unembedded, embs):
                self._mem_embed[item["gold_id"]] = emb
        (q_emb,) = self._embed([query])
        scored = []
        for item in gold_items:
            sim = self.cosine(q_emb, self._mem_embed[item["gold_id"]])
            scored.append((sim, item))
        scored.sort(reverse=True)
        return [item for sim, item in scored if sim >= self.SIM_THRESHOLD]


def evaluate_nodes_against_gold(
    data: dict[str, Any],
    gold_data: dict[str, Any],
    gold_path: Path,
    semantic_match: bool = False,
) -> dict[str, Any]:
    source_file = Path(str(data.get("source_path", ""))).name
    graphs = gold_data.get("graphs", [])
    selected = next(
        (graph for graph in graphs if str(graph.get("source_file", "")) == source_file),
        None,
    )
    if selected is None:
        raise SystemExit(f"Gold file {gold_path} has no graph annotation for source {source_file!r}")

    gold_nodes = flatten_gold_node_groups(selected.get("node_groups", []))
    must_nodes = [item for item in gold_nodes if item["requirement"] == "M"]
    optional_nodes = [item for item in gold_nodes if item["requirement"] == "O"]
    forbidden_nodes = [item for item in gold_nodes if item["requirement"] == "X"]
    predicted_nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []

    matched_must: dict[str, dict[str, Any]] = {}
    matched_optional: dict[str, dict[str, Any]] = {}
    false_positives: list[dict[str, Any]] = []
    match_layer_counts: Counter = Counter()

    semantic_matcher: SemanticNameMatcher | None = None
    if semantic_match:
        semantic_matcher = SemanticNameMatcher()

    gold_pool = [*must_nodes, *optional_nodes]

    def same_claim_kind_pool(predicted: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            item
            for item in gold_pool
            if item.get("type") == predicted.get("type")
            and (
                predicted.get("type") != "Claim"
                or item.get("claim_kind") == predicted.get("claim_kind")
            )
        ]

    for predicted in predicted_nodes:
        candidates = [item for item in gold_pool if gold_node_matches(predicted, item)]
        candidates.sort(key=lambda item: (item["requirement"] != "M", item["gold_id"]))
        chosen: dict[str, Any] | None = None
        layer = "rule"
        if candidates:
            chosen = candidates[0]
        if chosen is None:
            fuzzy = fuzzy_gold_candidates(predicted, gold_pool)
            if fuzzy:
                chosen = fuzzy[0]
                layer = "fuzzy"
        if chosen is None and semantic_matcher is not None:
            for candidate in semantic_matcher.best_candidates(
                predicted, same_claim_kind_pool(predicted)
            ):
                if semantic_matcher.judge_same(predicted, candidate):
                    chosen = candidate
                    layer = "semantic"
                    break
        if chosen is not None:
            match_layer_counts[layer] += 1
            matched = matched_must if chosen["requirement"] == "M" else matched_optional
            if chosen["gold_id"] in matched:
                false_positives.append(
                    {
                        "predicted": node_label(predicted),
                        "reason": "duplicate prediction for one gold node",
                        "gold_id": chosen["gold_id"],
                    }
                )
            else:
                matched[chosen["gold_id"]] = predicted
            continue

        forbidden = next(
            (item for item in forbidden_nodes if gold_node_matches(predicted, item)),
            None,
        )
        if forbidden is None:
            fuzzy_forbidden = fuzzy_gold_candidates(predicted, forbidden_nodes)
            if fuzzy_forbidden:
                forbidden = fuzzy_forbidden[0]
        if forbidden is None and semantic_matcher is not None:
            forbidden = next(
                (
                    item
                    for item in forbidden_nodes
                    if item.get("type") == predicted.get("type")
                    and semantic_matcher.judge_same(predicted, item)
                ),
                None,
            )
        false_positives.append(
            {
                "predicted": node_label(predicted),
                "reason": "explicitly forbidden" if forbidden else "not present in reviewed gold",
                "gold_id": forbidden["gold_id"] if forbidden else None,
            }
        )

    false_negatives = [
        gold_node_summary(item)
        for item in must_nodes
        if item["gold_id"] not in matched_must
    ]
    true_positive_count = len(matched_must)
    false_positive_count = len(false_positives)
    false_negative_count = len(false_negatives)
    precision = safe_ratio(true_positive_count, true_positive_count + false_positive_count)
    recall = safe_ratio(true_positive_count, true_positive_count + false_negative_count)
    f1 = safe_ratio(2 * precision * recall, precision + recall)

    per_type = {}
    for node_type in sorted(SEMANTIC_NODE_TYPES):
        type_must = [item for item in must_nodes if item["type"] == node_type]
        type_tp = sum(item["gold_id"] in matched_must for item in type_must)
        type_fn = len(type_must) - type_tp
        type_fp = sum(
            item["predicted"].get("type") == node_type for item in false_positives
        )
        type_precision = safe_ratio(type_tp, type_tp + type_fp)
        type_recall = safe_ratio(type_tp, type_tp + type_fn)
        per_type[node_type] = {
            "true_positive": type_tp,
            "false_positive": type_fp,
            "false_negative": type_fn,
            "precision": type_precision,
            "recall": type_recall,
            "f1": safe_ratio(2 * type_precision * type_recall, type_precision + type_recall),
        }

    return {
        "gold_file": str(gold_path),
        "gold_schema_version": gold_data.get("schema_version"),
        "source_file": source_file,
        "annotation_status": selected.get("annotation_status"),
        "node_metrics": {
            "must_gold_count": len(must_nodes),
            "optional_gold_count": len(optional_nodes),
            "forbidden_gold_count": len(forbidden_nodes),
            "predicted_count": len(predicted_nodes),
            "true_positive": true_positive_count,
            "false_positive": false_positive_count,
            "false_negative": false_negative_count,
            "optional_matched": len(matched_optional),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "per_type": per_type,
            "match_layers": dict(match_layer_counts),
        },
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_positive_matches": [
            {
                "gold_id": gold_id,
                "predicted": node_label(predicted),
            }
            for gold_id, predicted in sorted(matched_must.items())
        ],
        "optional_matches": [
            {
                "gold_id": gold_id,
                "predicted": node_label(predicted),
            }
            for gold_id, predicted in sorted(matched_optional.items())
        ],
        "relation_metrics": None,
        "relation_annotation_status": selected.get("relation_annotation_status", "pending"),
    }


def flatten_gold_node_groups(groups: Any) -> list[dict[str, Any]]:
    flattened = []
    if not isinstance(groups, list):
        return flattened
    for group in groups:
        if not isinstance(group, dict):
            continue
        requirement = group.get("requirement")
        node_type = group.get("type")
        claim_kind = group.get("claim_kind")
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            flattened.append(
                {
                    **item,
                    "requirement": requirement,
                    "type": node_type,
                    "claim_kind": claim_kind,
                }
            )
    return flattened


def fuzzy_similarity(name_a: str, name_b: str) -> float:
    """Character-level name similarity on canonical forms, for all node types.

    Combines difflib ratio (in-order character similarity) with bigram Jaccard
    (tolerant of word-order shuffling). Both operate on canonical_node_name
    form, so LaTeX/whitespace/punctuation variants cost nothing here.
    """
    a = canonical_node_name(str(name_a))
    b = canonical_node_name(str(name_b))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    bigrams_a = {a[i : i + 2] for i in range(len(a) - 1)}
    bigrams_b = {b[i : i + 2] for i in range(len(b) - 1)}
    if not bigrams_a or not bigrams_b:
        return ratio
    jaccard = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)
    return max(ratio, jaccard)


FUZZY_MATCH_THRESHOLD = 0.85

# Single-character mathematical modifiers that change the identity of an
# object (反函数 vs 函数, 左极限 vs 右极限, 上界 vs 下界, 有界 vs 无界).
# Fuzzy string similarity cannot be trusted across a mismatch here.
CRITICAL_MODIFIERS = "反左右上下正负奇偶增减内外"


def critical_modifier_mismatch(name_a: str, name_b: str) -> bool:
    set_a = {c for c in canonical_node_name(str(name_a)) if c in CRITICAL_MODIFIERS}
    set_b = {c for c in canonical_node_name(str(name_b)) if c in CRITICAL_MODIFIERS}
    return bool(set_a.symmetric_difference(set_b))


def fuzzy_gold_candidates(
    predicted: dict[str, Any],
    gold_items: list[dict[str, Any]],
    threshold: float = FUZZY_MATCH_THRESHOLD,
) -> list[dict[str, Any]]:
    """Rescue surface-variant matches for Concept, Claim, and Method nodes.

    Same type as predicted; Claim candidates additionally require the same
    claim_kind. Scored on the best name/alias pair similarity; returns all
    candidates above threshold, M before O, stable by gold_id.
    """
    pool = [
        item
        for item in gold_items
        if item.get("type") == predicted.get("type")
        and (
            predicted.get("type") != "Claim"
            or item.get("claim_kind") == predicted.get("claim_kind")
        )
    ]
    predicted_names = [predicted.get("name", "")]
    if isinstance(predicted.get("aliases"), list):
        predicted_names.extend(predicted["aliases"])
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in pool:
        gold_names = [item.get("name", "")]
        if isinstance(item.get("accepted_names"), list):
            gold_names.extend(item["accepted_names"])
        pair_scores = [
            fuzzy_similarity(p, g)
            for p in predicted_names
            if str(p).strip()
            for g in gold_names
            if str(g).strip()
            and not critical_modifier_mismatch(p, g)
        ]
        if not pair_scores:
            continue
        best = max(pair_scores)
        if best >= threshold:
            scored.append((best, item))
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1].get("requirement") != "M",
            pair[1].get("gold_id", ""),
        )
    )
    return [item for _, item in scored]


def gold_node_matches(predicted: dict[str, Any], gold: dict[str, Any]) -> bool:
    if predicted.get("type") != gold.get("type"):
        return False
    if gold.get("type") == "Claim" and predicted.get("claim_kind") != gold.get("claim_kind"):
        return False
    predicted_names = [predicted.get("name", "")]
    if isinstance(predicted.get("aliases"), list):
        predicted_names.extend(predicted["aliases"])
    accepted_names = [gold.get("name", "")]
    if isinstance(gold.get("accepted_names"), list):
        accepted_names.extend(gold["accepted_names"])
    predicted_keys = {
        canonical_node_name(str(name)) for name in predicted_names if str(name).strip()
    }
    accepted_keys = {
        canonical_node_name(str(name)) for name in accepted_names if str(name).strip()
    }
    return bool(predicted_keys & accepted_keys)


def gold_node_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "gold_id": item.get("gold_id"),
        "type": item.get("type"),
        "claim_kind": item.get("claim_kind"),
        "name": item.get("name"),
    }


def _gold_alignment_lookup(gold_nodes: list[dict[str, Any]]) -> dict[tuple[Any, Any, str], str]:
    """Map reviewed (type, claim_kind, canonical name) to gold_id for endpoint alignment.

    Only M and O items are indexed; X (forbidden) items have no reviewed id to
    align to. When a canonical key collides between items, M wins over O,
    mirroring the priority used by evaluate_nodes_against_gold.
    Within the same requirement level, two items with the same canonical key
    that map to different gold_ids raise a ValueError.
    """
    lookup: dict[tuple[Any, Any, str], str] = {}
    set_by: dict[tuple[Any, Any, str], str] = {}
    for item in sorted(
        (entry for entry in gold_nodes if entry.get("requirement") != "X"),
        key=lambda entry: (entry.get("requirement") == "M", entry.get("gold_id", "")),
    ):
        requirement = item.get("requirement")
        names = [item.get("name", "")]
        if isinstance(item.get("accepted_names"), list):
            names.extend(item["accepted_names"])
        for name in names:
            canonical = canonical_node_name(str(name))
            if not canonical:
                continue
            key = (item.get("type"), item.get("claim_kind"), canonical)
            if key in lookup and set_by[key] == requirement and lookup[key] != item.get("gold_id"):
                raise ValueError(
                    f"Conflicting gold {requirement} items map to {key!r}: "
                    f"gold_id {lookup[key]!r} vs {item.get('gold_id')!r}"
                )
            lookup[key] = item.get("gold_id")
            set_by[key] = requirement
    return lookup


def gold_id_for_predicted_node(
    predicted: dict[str, Any], lookup: dict[tuple[Any, Any, str], str]
) -> str | None:
    claim_kind = predicted.get("claim_kind") if predicted.get("type") == "Claim" else None
    names = [predicted.get("name", "")]
    if isinstance(predicted.get("aliases"), list):
        names.extend(predicted["aliases"])
    for name in names:
        canonical = canonical_node_name(str(name))
        if not canonical:
            continue
        gold_id = lookup.get((predicted.get("type"), claim_kind, canonical))
        if gold_id is not None:
            return gold_id
    return None


def gold_aligned_relation_report(
    data: dict[str, Any], gold_data: dict[str, Any], gold_path: Path
) -> dict[str, Any]:
    """Map one graph's relation endpoints to reviewed gold ids.

    raw_edges keeps the model-generated node names for the legacy raw-name
    metric; aligned_edges requires both endpoints to resolve to a reviewed
    gold id and drops relations that do not.
    """
    source_file = Path(str(data.get("source_path", ""))).name
    graphs = gold_data.get("graphs", [])
    selected = next(
        (graph for graph in graphs if str(graph.get("source_file", "")) == source_file),
        None,
    )
    if selected is None:
        raise SystemExit(f"Gold file {gold_path} has no graph annotation for source {source_file!r}")

    gold_nodes = flatten_gold_node_groups(selected.get("node_groups", []))
    lookup = _gold_alignment_lookup(gold_nodes)
    nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    relations = data.get("relations", []) if isinstance(data.get("relations"), list) else []

    raw_edges: set[tuple[str, str, str]] = set()
    aligned_edges: set[tuple[str, str, str]] = set()
    dropped: list[dict[str, Any]] = []
    for rel in relations:
        source_id = rel.get("source_id")
        target_id = rel.get("target_id")
        rel_type = rel.get("type")
        if source_id not in node_by_id or target_id not in node_by_id:
            dropped.append(
                {
                    "relation_id": rel.get("id"),
                    "relation_type": rel_type,
                    "source_id": source_id,
                    "target_id": target_id,
                    "reason": "endpoint missing from predicted nodes",
                }
            )
            continue
        source_node = node_by_id[source_id]
        target_node = node_by_id[target_id]
        raw_edges.add(
            (
                str(source_node.get("name", "")),
                str(rel_type),
                str(target_node.get("name", "")),
            )
        )
        source_gold = gold_id_for_predicted_node(source_node, lookup)
        target_gold = gold_id_for_predicted_node(target_node, lookup)
        if source_gold is None or target_gold is None:
            dropped.append(
                {
                    "relation_id": rel.get("id"),
                    "relation_type": rel_type,
                    "source_id": source_id,
                    "target_id": target_id,
                    "source_name": str(source_node.get("name", "")),
                    "target_name": str(target_node.get("name", "")),
                    "reason": "endpoint without a reviewed gold id",
                }
            )
            continue
        aligned_edges.add((source_gold, str(rel_type), target_gold))

    return {
        "source_file": source_file,
        "relation_annotation_status": selected.get("relation_annotation_status", "pending"),
        "relation_count": len(relations),
        "raw_edge_count": len(raw_edges),
        "aligned_edge_count": len(aligned_edges),
        "dropped_relation_count": len(dropped),
        "alignment_coverage": safe_ratio(len(aligned_edges), len(relations)),
        "dropped_relations": dropped,
        "raw_edges": sorted(raw_edges),
        "aligned_edges": sorted(aligned_edges),
    }


def evaluate_relation_stability(
    graphs: list[dict[str, Any]], gold_data: dict[str, Any], gold_path: Path
) -> dict[str, Any]:
    """Cross-run relation-set stability using gold-aligned endpoints.

    Jaccard is computed twice per run pair: raw_name uses the model-generated
    endpoint names and therefore counts naming drift as instability;
    gold_aligned maps endpoints to reviewed gold ids first. relation_metrics
    stays null because gold relations are not annotated yet: stability is not
    precision, recall, or F1.
    """
    if not graphs:
        raise SystemExit("Relation stability evaluation requires at least one KG JSON")
    source_files = {Path(str(g.get("source_path", ""))).name for g in graphs}
    if len(source_files) > 1:
        raise SystemExit(
            "Relation stability evaluation requires runs of one source section, "
            f"got: {sorted(source_files)}"
        )

    reports = [gold_aligned_relation_report(g, gold_data, gold_path) for g in graphs]
    raw_edge_sets = [set(r["raw_edges"]) for r in reports]
    aligned_edge_sets = [set(r["aligned_edges"]) for r in reports]

    pairwise = []
    for i in range(len(reports)):
        for j in range(i + 1, len(reports)):
            raw_intersection = raw_edge_sets[i] & raw_edge_sets[j]
            raw_union = raw_edge_sets[i] | raw_edge_sets[j]
            aligned_intersection = aligned_edge_sets[i] & aligned_edge_sets[j]
            aligned_union = aligned_edge_sets[i] | aligned_edge_sets[j]
            pairwise.append(
                {
                    "run_a": i + 1,
                    "run_b": j + 1,
                    "raw_name_jaccard": safe_ratio(len(raw_intersection), len(raw_union)),
                    "gold_aligned_jaccard": safe_ratio(
                        len(aligned_intersection), len(aligned_union)
                    ),
                    "raw_intersection_count": len(raw_intersection),
                    "raw_union_count": len(raw_union),
                    "gold_aligned_intersection_count": len(aligned_intersection),
                    "gold_aligned_union_count": len(aligned_union),
                }
            )

    stable_raw = set.intersection(*raw_edge_sets) if raw_edge_sets else set()
    stable_aligned = set.intersection(*aligned_edge_sets) if aligned_edge_sets else set()

    return {
        "gold_file": str(gold_path),
        "gold_schema_version": gold_data.get("schema_version"),
        "source_file": sorted(source_files)[0] if source_files else None,
        "run_count": len(reports),
        "relation_annotation_status": reports[0]["relation_annotation_status"],
        "runs": [
            {
                "run_index": i + 1,
                "relation_count": r["relation_count"],
                "raw_edge_count": r["raw_edge_count"],
                "aligned_edge_count": r["aligned_edge_count"],
                "dropped_relation_count": r["dropped_relation_count"],
                "alignment_coverage": r["alignment_coverage"],
                "dropped_relations": r["dropped_relations"],
            }
            for i, r in enumerate(reports)
        ],
        "pairwise": pairwise,
        "stable_edges": {
            "raw_name": sorted(stable_raw),
            "gold_aligned": sorted(stable_aligned),
        },
        "edge_union_counts": {
            "raw_name": len(set().union(*raw_edge_sets)) if raw_edge_sets else 0,
            "gold_aligned": len(set().union(*aligned_edge_sets)) if aligned_edge_sets else 0,
        },
        "relation_metrics": None,
    }


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


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
        "claim_kind": node.get("claim_kind"),
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
    if "math_expression_count" in counts:
        print(f"Math expressions: {counts['math_expression_count']}")
        print(
            "Connected components: "
            f"{counts['connected_component_count']} {counts['connected_component_sizes']}"
        )
    print(f"Severe issues: {summary['severe_issue_count']}")
    print(f"Warning issues: {summary['warning_issue_count']}")
    print(f"Structurally clean: {summary['is_structurally_clean']}")
    gold = report.get("gold_evaluation")
    if gold:
        metrics = gold["node_metrics"]
        print(
            "Gold node P/R/F1: "
            f"{metrics['precision']:.4f} / {metrics['recall']:.4f} / {metrics['f1']:.4f} "
            f"(TP={metrics['true_positive']}, FP={metrics['false_positive']}, "
            f"FN={metrics['false_negative']}, optional={metrics['optional_matched']})"
        )
        print(f"Gold relation status: {gold['relation_annotation_status']}")
    stability = report.get("relation_stability")
    if stability:
        print(f"Relation stability: {stability['run_count']} runs of {stability['source_file']}")
        print(f"  Gold relation status: {stability['relation_annotation_status']}")
        for pair in stability["pairwise"]:
            print(
                f"  Jaccard r{pair['run_a']}-r{pair['run_b']}: "
                f"raw_name={pair['raw_name_jaccard']:.4f} "
                f"gold_aligned={pair['gold_aligned_jaccard']:.4f}"
            )
        for run in stability["runs"]:
            print(
                f"  Run {run['run_index']}: {run['aligned_edge_count']}/{run['relation_count']} aligned edges "
                f"(coverage {run['alignment_coverage']:.2%}, dropped {run['dropped_relation_count']})"
            )
        print(
            "  Stable gold-aligned edges: "
            f"{len(stability['stable_edges']['gold_aligned'])} "
            f"(union {stability['edge_union_counts']['gold_aligned']})"
        )
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
