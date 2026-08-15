from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    gold_data = None
    gold_path = None
    if args.gold:
        gold_path = Path(args.gold)
        gold_data = load_json(gold_path)
        gold_reports = evaluate_gold_scopes(
            data,
            gold_data,
            gold_path,
            semantic_match=args.semantic_match,
            gold_source_file=args.gold_source,
        )
        if len(gold_reports) == 1:
            report["gold_evaluation"] = gold_reports[0]
        else:
            report["gold_scope_evaluations"] = gold_reports
    if args.relation_gold:
        if not args.gold:
            raise SystemExit("Relation gold evaluation requires --gold for endpoint alignment")
        relation_gold_path = Path(args.relation_gold)
        relation_gold_data = load_json(relation_gold_path)
        report["relation_gold_evaluation"] = evaluate_relations_against_gold(
            data,
            relation_gold_data,
            gold_data,
            relation_gold_path,
            semantic_match=args.semantic_match,
        )
    if args.additional_inputs:
        if not args.gold:
            raise SystemExit("Relation stability evaluation requires --gold for endpoint alignment")
        graphs = [data, *(load_json(Path(p)) for p in args.additional_inputs)]
        report["relation_stability"] = evaluate_relation_stability(
            graphs,
            gold_data,
            gold_path,
            gold_source_file=args.gold_source,
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report, args.samples)
    if args.fail_on_severe and report["summary"]["severe_issue_count"] > 0:
        return 1
    return 0

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a MaF-KG semantic KG JSON output.")
    parser.add_argument(
        "input",
        nargs="?",
        help="KG JSON file. If omitted, the latest supported JSON file under --kg-dir is used.",
    )
    parser.add_argument(
        "additional_inputs",
        nargs="*",
        help="Additional KG JSON runs of the same source document for cross-run relation "
        "stability evaluation (requires --gold). Gold source scopes are evaluated separately.",
    )
    parser.add_argument("--kg-dir", default="KG", help="Directory used when input is omitted.")
    parser.add_argument("--json", action="store_true", help="Print full machine-readable JSON report.")
    parser.add_argument(
        "--gold",
        help="Optional reviewed semantic Gold JSON used to calculate node precision, recall, and F1.",
    )
    parser.add_argument(
        "--gold-source",
        help=(
            "Evaluate only the named source_file entry in Gold. This is optional for a "
            "single-section output and useful for selecting one declared scope from a "
            "full-chapter output."
        ),
    )
    parser.add_argument(
        "--relation-gold",
        help=(
            "Optional reviewed positive relation set JSON (e.g. relations_section2_v1.json). "
            "Requires --gold for endpoint alignment. Reports recall plus wrong_type / "
            "wrong_direction / missing_endpoint / missing_edge; because relation gold is "
            "not a closed negative set, precision and F1 are not reported."
        ),
    )
    parser.add_argument("--samples", type=int, default=12, help="Number of sample issues to print per category.")
    parser.add_argument(
        "--fail-on-severe",
        action="store_true",
        help="Exit with code 1 when severe structural/schema issues are found.",
    )
    parser.add_argument(
        "--semantic-match",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fall back to embedding retrieval + LLM judge for gold name matching "
        "when canonical rule + fuzzy matching fail. Enabled by default; use "
        "--no-semantic-match to disable. Requires local ollama qwen3-embedding "
        "and the OPENCODE_API_KEY environment variable.",
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
    schema_version = str(data.get("schema_version", ""))
    if not schema_version.startswith("maf-kg.semantic."):
        raise SystemExit(
            f"Unsupported schema_version {schema_version!r}: only maf-kg.semantic.* graphs are supported"
        )
    return evaluate_semantic_graph(data, path)

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
    JUDGE_ENDPOINTS: tuple[tuple[str, str], ...] = (
        ("https://opencode.ai/zen/go/v1/chat/completions", "OPENCODE_API_KEY"),
        ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
    )
    SIM_THRESHOLD = 0.65
    TOP_K = 3

    def __init__(self) -> None:
        self._mem_embed: dict[str, list[float]] = {}
        self._judge_cache: dict[tuple[str, str], bool] = {}
        self._call_count = 0

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self.OLLAMA_URL,
            data=json.dumps({"model": self.EMBED_MODEL, "input": texts}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())["embeddings"]
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"semantic matching requires a local ollama serving {self.EMBED_MODEL} "
                f"at {self.OLLAMA_URL}; start it or pass --no-semantic-match. ({exc})"
            ) from exc

    def _judge(self, name_a: str, name_b: str, desc_a: str, desc_b: str) -> bool:
        key = (name_a, name_b, desc_a, desc_b)
        if key in self._judge_cache:
            return self._judge_cache[key]
        import os
        import urllib.error
        import urllib.request

        sys_prompt = (
            "你是数学知识图谱的命题判定器。给定两个数学对象（各有一个名称和一个"
            "完整描述/命题），判断它们是否指代同一个数学对象。以完整描述/命题为准，"
            "名称只是辅助线索：同一对象可有不同名称（同义改写、措辞不同、含 f(x) "
            "与否、“x→+∞”与“x趋于正无穷”等写法差异），只要两个描述表述同一命题就判"
            "同一；名称相近但命题不同（如左极限与右极限、一个定理与其推论、概念与"
            "其性质）应判不同。"
            '只输出 JSON：{"same": true/false, "reason": "一句话理由"}'
        )
        user_prompt = (
            f'对象A：名称"{name_a}"；命题/描述：{desc_a}\n'
            f'对象B：名称"{name_b}"；命题/描述：{desc_b}\n'
            "判断二者是否同一数学对象。"
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

        errors: list[str] = []
        for url, key_env in self.JUDGE_ENDPOINTS:
            api_key = os.getenv(key_env)
            if not api_key:
                errors.append(f"{key_env} not set")
                continue
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    response = json.loads(resp.read())
                if response.get("code") not in (None, 0):
                    errors.append(f"{url} API error {response.get('code')}: {response.get('message')}")
                    continue
                content = response["choices"][0]["message"]["content"]
                self._call_count += 1
                try:
                    verdict = json.loads(content).get("same", False)
                except json.JSONDecodeError:
                    verdict = False
                self._judge_cache[key] = verdict
                return verdict
            except urllib.error.URLError as exc:
                errors.append(f"{url} unreachable: {exc}")
                continue
        raise SystemExit("semantic judge failed on all endpoints: " + "; ".join(errors))

    def cosine(self, a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b)) / (
            (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        )

    def judge_same(self, predicted: dict[str, Any], gold_item: dict[str, Any]) -> bool:
        """Ask the flash judge whether predicted and gold nodes denote the same object.

        The judge compares the complete proposition/description of both sides,
        not just names, so a concise predicted name can still match a gold node
        whose proposition is identical.
        """
        name_a = str(predicted.get("name", ""))
        name_b = str(gold_item.get("name", ""))
        desc_a = str(predicted.get("description", ""))[:400]
        desc_b = str(gold_item.get("description", ""))[:400]
        return self._judge(name_a, name_b, desc_a, desc_b)

    @staticmethod
    def _embed_text(node: dict[str, Any]) -> str:
        name = str(node.get("name", ""))
        desc = str(node.get("description", ""))
        return (name + " " + desc).strip() or name

    def best_candidates(self, predicted: dict[str, Any], gold_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank same-type gold items by embedding similarity to name + description.

        Returns at most ``TOP_K`` candidates above ``SIM_THRESHOLD``, so the
        number of LLM judge calls per query is bounded.
        """
        if predicted.get("type") not in ("Concept", "Claim", "Method"):
            return []
        query = self._embed_text(predicted)
        if not query.strip():
            return []
        unembedded = [item for item in gold_items if item["gold_id"] not in self._mem_embed]
        if unembedded:
            embs = self._embed([self._embed_text(item) for item in unembedded])
            for item, emb in zip(unembedded, embs):
                self._mem_embed[item["gold_id"]] = emb
        (q_emb,) = self._embed([query])
        scored = []
        for item in gold_items:
            sim = self.cosine(q_emb, self._mem_embed[item["gold_id"]])
            scored.append((sim, item))
        scored.sort(reverse=True)
        candidates = [item for sim, item in scored if sim >= self.SIM_THRESHOLD]
        return candidates[: self.TOP_K]

def evaluate_nodes_against_gold(
    data: dict[str, Any],
    gold_data: dict[str, Any],
    gold_path: Path,
    semantic_match: bool = True,
    gold_source_file: str | None = None,
) -> dict[str, Any]:
    source_file = Path(str(data.get("source_path", ""))).name
    graphs = gold_data.get("graphs", [])
    targets = gold_targets_for_source(source_file, graphs)
    if gold_source_file is not None:
        selected_source = Path(gold_source_file).name
        targets = [
            target
            for target in targets
            if Path(str(target[0].get("source_file", ""))).name == selected_source
        ]
        if not targets:
            raise SystemExit(
                f"Gold file {gold_path} has no annotation {selected_source!r} applicable "
                f"to source {source_file!r}"
            )
    if not targets:
        raise SystemExit(f"Gold file {gold_path} has no graph annotation for source {source_file!r}")
    if len(targets) > 1:
        choices = [str(target[0].get("source_file", "")) for target in targets]
        raise SystemExit(
            f"Gold file {gold_path} has multiple scoped annotations for source {source_file!r}: "
            f"{choices}. Select one with --gold-source."
        )
    selected, source_scope = targets[0]

    gold_nodes = flatten_gold_node_groups(selected.get("node_groups", []))
    must_nodes = [item for item in gold_nodes if item["requirement"] == "M"]
    optional_nodes = [item for item in gold_nodes if item["requirement"] == "O"]
    forbidden_nodes = [item for item in gold_nodes if item["requirement"] == "X"]
    all_predicted_nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []
    predicted_nodes = filter_nodes_to_source_scope(
        all_predicted_nodes,
        source_file,
        source_scope,
    )

    matched_must: dict[str, dict[str, Any]] = {}
    matched_optional: dict[str, dict[str, Any]] = {}
    false_positives: list[dict[str, Any]] = []
    match_layer_counts: Counter = Counter()

    policy = MatchPolicy(semantic=semantic_match)
    semantic_matcher = SemanticNameMatcher() if semantic_match else None

    gold_pool = [*must_nodes, *optional_nodes]

    for predicted in predicted_nodes:
        chosen, layer = match_node_to_gold(predicted, gold_pool, policy, semantic_matcher)
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

    per_tier = {}
    tier_names = sorted(
        {str(item.get("evaluation_tier") or "unclassified") for item in must_nodes},
        key=lambda value: (value not in {"core", "detail"}, value),
    )
    for tier in tier_names:
        tier_must = [
            item
            for item in must_nodes
            if str(item.get("evaluation_tier") or "unclassified") == tier
        ]
        tier_matched = sum(item["gold_id"] in matched_must for item in tier_must)
        per_tier[tier] = {
            "gold_count": len(tier_must),
            "matched": tier_matched,
            "false_negative": len(tier_must) - tier_matched,
            "recall": safe_ratio(tier_matched, len(tier_must)),
        }

    return {
        "gold_file": str(gold_path),
        "gold_schema_version": gold_data.get("schema_version"),
        "source_file": source_file,
        "gold_source_file": selected.get("source_file"),
        "source_scope": source_scope,
        "annotation_status": selected.get("annotation_status"),
        "node_metrics": {
            "must_gold_count": len(must_nodes),
            "optional_gold_count": len(optional_nodes),
            "forbidden_gold_count": len(forbidden_nodes),
            "predicted_count": len(predicted_nodes),
            "predicted_total_count": len(all_predicted_nodes),
            "true_positive": true_positive_count,
            "false_positive": false_positive_count,
            "false_negative": false_negative_count,
            "optional_matched": len(matched_optional),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "per_type": per_type,
            "per_tier": per_tier,
            "match_layers": dict(match_layer_counts),
        },
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_positive_matches": [
            {
                "gold_id": gold_id,
                "evaluation_tier": next(
                    (
                        item.get("evaluation_tier") or "unclassified"
                        for item in must_nodes
                        if item["gold_id"] == gold_id
                    ),
                    "unclassified",
                ),
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

def evaluate_gold_scopes(
    data: dict[str, Any],
    gold_data: dict[str, Any],
    gold_path: Path,
    *,
    semantic_match: bool = True,
    gold_source_file: str | None = None,
) -> list[dict[str, Any]]:
    source_file = Path(str(data.get("source_path", ""))).name
    targets = gold_targets_for_source(source_file, gold_data.get("graphs", []))
    if gold_source_file is not None:
        selected_source = Path(gold_source_file).name
        targets = [
            target
            for target in targets
            if Path(str(target[0].get("source_file", ""))).name == selected_source
        ]
    if not targets:
        detail = f" for Gold source {gold_source_file!r}" if gold_source_file else ""
        raise SystemExit(
            f"Gold file {gold_path} has no graph annotation{detail} applicable to "
            f"source {source_file!r}"
        )
    return [
        evaluate_nodes_against_gold(
            data,
            gold_data,
            gold_path,
            semantic_match=semantic_match,
            gold_source_file=str(graph.get("source_file", "")),
        )
        for graph, _ in targets
    ]

def gold_targets_for_source(
    source_file: str,
    graphs: Any,
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    if not isinstance(graphs, list):
        return []
    source_name = Path(source_file).name
    exact = [
        (graph, None)
        for graph in graphs
        if isinstance(graph, dict)
        and Path(str(graph.get("source_file", ""))).name == source_name
    ]
    if exact:
        return exact

    scoped: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        for scope in graph.get("source_scopes", []):
            if not isinstance(scope, dict):
                continue
            if Path(str(scope.get("source_file", ""))).name != source_name:
                continue
            start_line = scope.get("start_line")
            end_line = scope.get("end_line")
            if not isinstance(start_line, int) or not isinstance(end_line, int):
                raise ValueError(
                    f"Invalid source scope for {graph.get('source_file')!r}: "
                    "start_line and end_line must be integers"
                )
            if start_line < 1 or end_line < start_line:
                raise ValueError(
                    f"Invalid source scope for {graph.get('source_file')!r}: "
                    f"L{start_line}-L{end_line}"
                )
            scoped.append((graph, dict(scope)))
    return scoped

def filter_nodes_to_source_scope(
    nodes: list[dict[str, Any]],
    source_file: str,
    source_scope: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if source_scope is None:
        return list(nodes)
    return [
        node
        for node in nodes
        if evidence_within_source_scope(
            node.get("evidence", []), source_file, source_scope
        )
    ]

def evidence_within_source_scope(
    evidence_items: Any,
    source_file: str,
    source_scope: dict[str, Any] | None,
) -> bool:
    if source_scope is None:
        return True
    if not isinstance(evidence_items, list):
        return False
    start_line = int(source_scope["start_line"])
    end_line = int(source_scope["end_line"])
    source_name = Path(source_file).name
    return any(
        isinstance(evidence, dict)
        and Path(str(evidence.get("source_path", ""))).name == source_name
        and isinstance(evidence.get("start_line"), int)
        and isinstance(evidence.get("end_line"), int)
        and start_line <= evidence["start_line"] <= evidence["end_line"] <= end_line
        for evidence in evidence_items
    )

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
        group_tier = group.get("evaluation_tier")
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            flattened.append(
                {
                    **item,
                    "requirement": requirement,
                    "type": node_type,
                    "claim_kind": claim_kind,
                    "evaluation_tier": item.get("evaluation_tier", group_tier),
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

@dataclass(frozen=True)
class MatchPolicy:
    """Global gold node-matching preset shared by node and relation evaluation.

    ``fuzzy`` enables character-level fuzzy fallback after the exact rule
    match; ``semantic`` enables embedding retrieval + LLM judge (requires a
    ``SemanticNameMatcher`` supplied by the caller). ``fuzzy_threshold`` is the
    minimum ``fuzzy_similarity`` for the fuzzy layer.
    """

    fuzzy: bool = True
    semantic: bool = True
    fuzzy_threshold: float = FUZZY_MATCH_THRESHOLD


#: Default preset: rule + fuzzy + semantic (semantic judge is on by default).
DEFAULT_MATCH_POLICY = MatchPolicy()


def match_node_to_gold(
    predicted: dict[str, Any],
    gold_items: list[dict[str, Any]],
    policy: MatchPolicy,
    semantic_matcher: SemanticNameMatcher | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Match a predicted node to the best eligible gold item under ``policy``.

    Layers, in order: rule (exact canonical name/alias) -> fuzzy -> semantic.
    Only M and O gold items are eligible; X items are forbidden and handled
    separately by callers. Returns ``(matched_item, layer)``; both ``None``
    when there is no match.
    """
    eligible = [item for item in gold_items if item.get("requirement") != "X"]

    exact = [item for item in eligible if gold_node_matches(predicted, item)]
    exact.sort(key=lambda item: (item["requirement"] != "M", item.get("gold_id", "")))
    if exact:
        return exact[0], "rule"

    if policy.fuzzy:
        fuzzy = fuzzy_gold_candidates(predicted, eligible, threshold=policy.fuzzy_threshold)
        if fuzzy:
            return fuzzy[0], "fuzzy"

    if policy.semantic and semantic_matcher is not None:
        pool = [
            item
            for item in eligible
            if item.get("type") == predicted.get("type")
            and (
                predicted.get("type") != "Claim"
                or item.get("claim_kind") == predicted.get("claim_kind")
            )
        ]
        for candidate in semantic_matcher.best_candidates(predicted, pool):
            if semantic_matcher.judge_same(predicted, candidate):
                return candidate, "semantic"
    return None, None

def gold_aligned_relation_report(
    data: dict[str, Any],
    selected_gold: dict[str, Any],
    source_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map one graph's relation endpoints to reviewed gold ids.

    raw_edges keeps the model-generated node names for the legacy raw-name
    metric; aligned_edges requires both endpoints to resolve to a reviewed
    gold id and drops relations that do not.
    """
    source_file = Path(str(data.get("source_path", ""))).name
    gold_nodes = flatten_gold_node_groups(selected_gold.get("node_groups", []))
    lookup = _gold_alignment_lookup(gold_nodes)
    all_nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []
    nodes = filter_nodes_to_source_scope(all_nodes, source_file, source_scope)
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    all_relations = data.get("relations", []) if isinstance(data.get("relations"), list) else []
    if source_scope is None:
        relations = all_relations
    else:
        scoped_node_ids = set(node_by_id)
        relations = [
            relation
            for relation in all_relations
            if relation.get("source_id") in scoped_node_ids
            and relation.get("target_id") in scoped_node_ids
            and evidence_within_source_scope(
                relation.get("evidence", []), source_file, source_scope
            )
        ]

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
        "gold_source_file": selected_gold.get("source_file"),
        "source_scope": source_scope,
        "relation_annotation_status": selected_gold.get(
            "relation_annotation_status", "pending"
        ),
        "predicted_total_node_count": len(all_nodes),
        "scoped_node_count": len(nodes),
        "predicted_total_relation_count": len(all_relations),
        "out_of_scope_relation_count": len(all_relations) - len(relations),
        "relation_count": len(relations),
        "raw_edge_count": len(raw_edges),
        "aligned_edge_count": len(aligned_edges),
        "dropped_relation_count": len(dropped),
        "alignment_coverage": safe_ratio(len(aligned_edges), len(relations)),
        "dropped_relations": dropped,
        "raw_edges": sorted(raw_edges),
        "aligned_edges": sorted(aligned_edges),
    }

def _relation_stability_for_gold_target(
    graphs: list[dict[str, Any]],
    selected_gold: dict[str, Any],
    source_scope: dict[str, Any] | None,
    gold_data: dict[str, Any],
    gold_path: Path,
) -> dict[str, Any]:
    reports = [
        gold_aligned_relation_report(graph, selected_gold, source_scope)
        for graph in graphs
    ]
    raw_edge_sets = [set(report["raw_edges"]) for report in reports]
    aligned_edge_sets = [set(report["aligned_edges"]) for report in reports]

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
        "source_file": reports[0]["source_file"],
        "gold_source_file": selected_gold.get("source_file"),
        "source_scope": source_scope,
        "run_count": len(reports),
        "relation_annotation_status": reports[0]["relation_annotation_status"],
        "runs": [
            {
                "run_index": i + 1,
                "predicted_total_node_count": report["predicted_total_node_count"],
                "scoped_node_count": report["scoped_node_count"],
                "predicted_total_relation_count": report[
                    "predicted_total_relation_count"
                ],
                "out_of_scope_relation_count": report[
                    "out_of_scope_relation_count"
                ],
                "relation_count": report["relation_count"],
                "raw_edge_count": report["raw_edge_count"],
                "aligned_edge_count": report["aligned_edge_count"],
                "dropped_relation_count": report["dropped_relation_count"],
                "alignment_coverage": report["alignment_coverage"],
                "dropped_relations": report["dropped_relations"],
            }
            for i, report in enumerate(reports)
        ],
        "pairwise": pairwise,
        "stable_edges": {
            "raw_name": sorted(stable_raw),
            "gold_aligned": sorted(stable_aligned),
        },
        "edge_union_counts": {
            "raw_name": len(set().union(*raw_edge_sets)) if raw_edge_sets else 0,
            "gold_aligned": len(set().union(*aligned_edge_sets))
            if aligned_edge_sets
            else 0,
        },
        "relation_metrics": None,
    }

def evaluate_relation_stability(
    graphs: list[dict[str, Any]],
    gold_data: dict[str, Any],
    gold_path: Path,
    gold_source_file: str | None = None,
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
            "Relation stability evaluation requires runs of one source document, "
            f"got: {sorted(source_files)}"
        )

    source_file = sorted(source_files)[0]
    targets = gold_targets_for_source(source_file, gold_data.get("graphs", []))
    if gold_source_file is not None:
        selected_name = Path(gold_source_file).name
        targets = [
            target
            for target in targets
            if Path(str(target[0].get("source_file", ""))).name == selected_name
        ]
    if not targets:
        scope_note = f" for Gold source {gold_source_file!r}" if gold_source_file else ""
        raise SystemExit(
            f"Gold file {gold_path} has no graph annotation applicable to "
            f"source {source_file!r}{scope_note}"
        )

    scope_reports = [
        _relation_stability_for_gold_target(
            graphs,
            selected_gold,
            source_scope,
            gold_data,
            gold_path,
        )
        for selected_gold, source_scope in targets
    ]
    if len(scope_reports) == 1:
        return scope_reports[0]
    return {
        "gold_file": str(gold_path),
        "gold_schema_version": gold_data.get("schema_version"),
        "source_file": source_file,
        "run_count": len(graphs),
        "scope_count": len(scope_reports),
        "scope_reports": scope_reports,
        "relation_metrics": None,
    }

def evaluate_relations_against_gold(
    data: dict[str, Any],
    relation_gold_data: dict[str, Any],
    node_gold_data: dict[str, Any],
    relation_gold_path: Path,
    semantic_match: bool = True,
) -> dict[str, Any]:
    """Evaluate predicted relations against a reviewed positive relation set.

    Endpoints are aligned to reviewed node gold ids first, then each positive
    relation is checked for a matching predicted edge (same source, type,
    target). Because the relation gold is not a closed negative set
    (``closed_world=false``), this reports recall plus error categories
    (wrong_type / wrong_direction / missing_endpoint / missing_edge) but never
    precision or F1.
    """
    relation_source = Path(str(relation_gold_data.get("source_file", ""))).name
    predicted_source = Path(str(data.get("source_path", ""))).name

    node_targets = gold_targets_for_source(
        relation_source or predicted_source, node_gold_data.get("graphs", [])
    )
    if not node_targets:
        raise SystemExit(
            "Node gold has no graph annotation applicable to relation gold "
            f"source {relation_source or predicted_source!r}"
        )
    selected_gold, source_scope = node_targets[0]

    gold_nodes = flatten_gold_node_groups(selected_gold.get("node_groups", []))
    policy = MatchPolicy(semantic=semantic_match)
    semantic_matcher = SemanticNameMatcher() if semantic_match else None

    nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []
    scoped_nodes = filter_nodes_to_source_scope(nodes, predicted_source, source_scope)
    node_by_id = {node.get("id"): node for node in scoped_nodes if node.get("id")}
    relations = data.get("relations", []) if isinstance(data.get("relations"), list) else []

    aligned_node_gold_ids: set[str] = set()
    for node in scoped_nodes:
        matched, _ = match_node_to_gold(node, gold_nodes, policy, semantic_matcher)
        if matched is not None:
            aligned_node_gold_ids.add(matched["gold_id"])

    aligned: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    dropped: list[dict[str, Any]] = []
    for rel in relations:
        source_node = node_by_id.get(rel.get("source_id"))
        target_node = node_by_id.get(rel.get("target_id"))
        if source_node is None or target_node is None:
            dropped.append(
                {"relation_id": rel.get("id"), "reason": "endpoint missing from predicted nodes"}
            )
            continue
        source_match, _ = match_node_to_gold(source_node, gold_nodes, policy, semantic_matcher)
        target_match, _ = match_node_to_gold(target_node, gold_nodes, policy, semantic_matcher)
        source_gold = source_match["gold_id"] if source_match else None
        target_gold = target_match["gold_id"] if target_match else None
        if source_gold is None or target_gold is None:
            dropped.append(
                {
                    "relation_id": rel.get("id"),
                    "type": rel.get("type"),
                    "source_name": str(source_node.get("name", "")),
                    "target_name": str(target_node.get("name", "")),
                    "reason": "endpoint without a reviewed gold id",
                }
            )
            continue
        aligned.setdefault((source_gold, rel.get("type"), target_gold), []).append(rel)

    positive = (
        relation_gold_data.get("relations", [])
        if isinstance(relation_gold_data.get("relations"), list)
        else []
    )

    matched_count = 0
    misses: list[dict[str, Any]] = []
    for gold_rel in positive:
        source_gold_id = gold_rel.get("source_gold_id")
        target_gold_id = gold_rel.get("target_gold_id")
        rel_type = gold_rel.get("type")
        if (source_gold_id, rel_type, target_gold_id) in aligned:
            matched_count += 1
            continue

        if source_gold_id not in aligned_node_gold_ids or target_gold_id not in aligned_node_gold_ids:
            status = "missing_endpoint"
            detail = (
                "endpoint without an aligned predicted node "
                f"(source={source_gold_id in aligned_node_gold_ids}, "
                f"target={target_gold_id in aligned_node_gold_ids})"
            )
        else:
            same_direction_types = {
                other_type
                for (src, other_type, tgt) in aligned
                if src == source_gold_id and tgt == target_gold_id
            }
            reversed_present = (target_gold_id, rel_type, source_gold_id) in aligned
            if same_direction_types:
                status = "wrong_type"
                detail = "predicted " + ", ".join(sorted(same_direction_types))
            elif reversed_present:
                status = "wrong_direction"
                detail = "predicted reversed direction"
            else:
                status = "missing_edge"
                detail = "both endpoints aligned but no predicted relation"
        misses.append(
            {
                "gold_id": gold_rel.get("gold_id"),
                "type": rel_type,
                "source_gold_id": source_gold_id,
                "target_gold_id": target_gold_id,
                "status": status,
                "detail": detail,
            }
        )

    positive_count = len(positive)
    miss_category_counts: dict[str, int] = {
        "wrong_type": 0,
        "wrong_direction": 0,
        "missing_endpoint": 0,
        "missing_edge": 0,
    }
    for miss in misses:
        miss_category_counts[miss["status"]] = miss_category_counts.get(miss["status"], 0) + 1

    return {
        "relation_gold_file": str(relation_gold_path),
        "relation_gold_schema_version": relation_gold_data.get("schema_version"),
        "annotation_status": relation_gold_data.get("annotation_status"),
        "closed_world": bool(relation_gold_data.get("closed_world", False)),
        "semantic_match": semantic_match,
        "source_file": relation_source or predicted_source,
        "note": (
            "closed_world=false: recall only; precision/F1 require a reviewed closed "
            "candidate universe and are not reported."
        ),
        "predicted_relation_count": len(relations),
        "aligned_relation_count": len(relations) - len(dropped),
        "dropped_relation_count": len(dropped),
        "dropped_relations": dropped,
        "positive_count": positive_count,
        "matched_count": matched_count,
        "recall": safe_ratio(matched_count, positive_count),
        "miss_category_counts": miss_category_counts,
        "misses": misses,
    }


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)

def duplicate_values(values: list[str]) -> list[tuple[str, int]]:
    counts = Counter(values)
    return sorted(
        [(key, count) for key, count in counts.items() if count > 1],
        key=lambda item: (-item[1], item[0]),
    )

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
    gold_reports = []
    if report.get("gold_evaluation"):
        gold_reports.append(report["gold_evaluation"])
    gold_reports.extend(report.get("gold_scope_evaluations", []))
    for gold in gold_reports:
        metrics = gold["node_metrics"]
        scope_label = gold.get("gold_source_file") or gold.get("source_file")
        prefix = f"Gold [{scope_label}]" if len(gold_reports) > 1 else "Gold"
        print(
            f"{prefix} node P/R/F1: "
            f"{metrics['precision']:.4f} / {metrics['recall']:.4f} / {metrics['f1']:.4f} "
            f"(TP={metrics['true_positive']}, FP={metrics['false_positive']}, "
            f"FN={metrics['false_negative']}, optional={metrics['optional_matched']})"
        )
        for tier, tier_metrics in metrics.get("per_tier", {}).items():
            print(
                f"  {tier} recall: {tier_metrics['recall']:.4f} "
                f"({tier_metrics['matched']}/{tier_metrics['gold_count']}, "
                f"FN={tier_metrics['false_negative']})"
            )
        print(f"{prefix} relation status: {gold['relation_annotation_status']}")
    stability = report.get("relation_stability")
    if stability:
        stability_reports = stability.get("scope_reports") or [stability]
        print(
            f"Relation stability: {stability['run_count']} runs of "
            f"{stability['source_file']}"
        )
        for scoped in stability_reports:
            scope_label = scoped.get("gold_source_file") or scoped["source_file"]
            if len(stability_reports) > 1:
                print(f"  Scope [{scope_label}]")
            print(
                "    Gold relation status: "
                f"{scoped['relation_annotation_status']}"
            )
            for pair in scoped["pairwise"]:
                print(
                    f"    Jaccard r{pair['run_a']}-r{pair['run_b']}: "
                    f"raw_name={pair['raw_name_jaccard']:.4f} "
                    f"gold_aligned={pair['gold_aligned_jaccard']:.4f}"
                )
            for run in scoped["runs"]:
                print(
                    f"    Run {run['run_index']}: "
                    f"{run['aligned_edge_count']}/{run['relation_count']} aligned edges "
                    f"(coverage {run['alignment_coverage']:.2%}, "
                    f"dropped {run['dropped_relation_count']})"
                )
            print(
                "    Stable gold-aligned edges: "
                f"{len(scoped['stable_edges']['gold_aligned'])} "
                f"(union {scoped['edge_union_counts']['gold_aligned']})"
            )
    relation_gold = report.get("relation_gold_evaluation")
    if relation_gold:
        print(
            f"Relation gold: {relation_gold['matched_count']}/{relation_gold['positive_count']} "
            f"(recall {relation_gold['recall']:.4f}, closed_world={relation_gold['closed_world']})"
        )
        for miss in relation_gold["misses"]:
            print(
                f"  [miss:{miss['status']}] {miss['gold_id']} "
                f"{miss['source_gold_id']} -[{miss['type']}]-> {miss['target_gold_id']} "
                f"({miss['detail']})"
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
