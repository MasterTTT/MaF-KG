"""Score a finite KU prerequisite evaluation without treating missing labels as negatives.

Input JSON example (all edge IDs refer to ``endpoints``)::

    {
      "endpoints": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
      "candidates": [
        {"source_ku": "B", "target_ku": "A"}
      ],
      "predictions": {
        "verdicts": [
          {"source_ku": "B", "target_ku": "A", "verdict": "accept"}
        ]
      },
      "reference": [
        {"prerequisite_ku": "A", "target_ku": "B", "role": "required",
         "directness": "direct", "provenance": "human"},
        {"prerequisite_ku": "B", "target_ku": "C", "role": "required",
         "directness": "indirect", "provenance": "draft"},
        {"prerequisite_ku": "A", "target_ku": "C", "role": "uncertain",
         "provenance": "draft"}
      ]
    }

Candidates and predictions store ``dependent depends_on prerequisite``; the
scorer reverses those fields to the canonical ``prerequisite -> target``
direction. Predictions may instead be ``{"depends_on": [...]}``, which means
the listed edges are the complete accepted output. Verdicts can be booleans,
``accept``/``reject``, or one of the reference role labels; ``required`` needs
``directness``. Reference roles are ``required`` (direct or indirect),
``principle_support``, ``proof_support``, ``related_or_none``, and ``uncertain``.
Every reference annotation requires a non-empty provenance string; ``draft``
and ``human`` are the recommended values.

Run with ``python scripts/score_ku_prerequisite_evaluation.py input.json`` or
add ``--output scores.json``. The module's ``score_evaluation`` function is
pure and does not read files, call services, or mutate its input.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import networkx as _nx


REFERENCE_ROLES = {
    "required",
    "principle_support",
    "proof_support",
    "related_or_none",
    "uncertain",
}
SUPPORT_ROLES = {"principle_support", "proof_support"}


def depends_on_to_prerequisite_of(edge: Mapping[str, Any]) -> tuple[str, str]:
    """Convert dependent ``source_ku`` depends_on prerequisite ``target_ku`` to A -> B."""
    source = edge.get("source_ku")
    target = edge.get("target_ku")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        raise ValueError("depends_on edge needs non-empty string source_ku and target_ku")
    return target, source


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be an object")
    return value


def _sequence(value: Any, where: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be an array")
    return value


def _endpoint_ids(raw: Any) -> set[str]:
    ids: set[str] = set()
    for index, item in enumerate(_sequence(raw, "endpoints")):
        if isinstance(item, str):
            endpoint_id = item
        else:
            endpoint_id = _mapping(item, f"endpoints[{index}]").get("id")
        if not isinstance(endpoint_id, str) or not endpoint_id:
            raise ValueError(f"endpoints[{index}] needs a non-empty string id")
        if endpoint_id in ids:
            raise ValueError(f"Duplicate endpoint id: {endpoint_id}")
        ids.add(endpoint_id)
    return ids


def _edge_rows(raw: Any, where: str) -> list[Any]:
    if isinstance(raw, Mapping):
        if "depends_on" not in raw or set(raw) != {"depends_on"}:
            raise ValueError(f"{where} object must contain only a depends_on array")
        raw = raw["depends_on"]
    return list(_sequence(raw, where))


def _parse_depends_on_edges(
    raw: Any, where: str, endpoint_ids: set[str]
) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for index, item in enumerate(_edge_rows(raw, where)):
        row = _mapping(item, f"{where}[{index}]")
        edge = depends_on_to_prerequisite_of(row)
        for endpoint_id in edge:
            if endpoint_id not in endpoint_ids:
                raise ValueError(f"Unknown endpoint {endpoint_id!r} in {where}[{index}]")
        if edge in result:
            raise ValueError(f"Duplicate directed edge in {where}: {edge[0]} -> {edge[1]}")
        result.add(edge)
    return result


def _parse_reference(raw: Any, endpoint_ids: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    if isinstance(raw, Mapping):
        if "annotations" not in raw or set(raw) != {"annotations"}:
            raise ValueError("reference object must contain only an annotations array")
        raw = raw["annotations"]
    annotations: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(_sequence(raw, "reference")):
        row = _mapping(item, f"reference[{index}]")
        prerequisite = row.get("prerequisite_ku")
        target = row.get("target_ku")
        if not isinstance(prerequisite, str) or not prerequisite:
            raise ValueError(f"reference[{index}] needs a non-empty prerequisite_ku")
        if not isinstance(target, str) or not target:
            raise ValueError(f"reference[{index}] needs a non-empty target_ku")
        for endpoint_id in (prerequisite, target):
            if endpoint_id not in endpoint_ids:
                raise ValueError(f"Unknown endpoint {endpoint_id!r} in reference[{index}]")

        role = row.get("role")
        if not isinstance(role, str) or role not in REFERENCE_ROLES:
            raise ValueError(f"Invalid role in reference[{index}]: {role!r}")
        directness = row.get("directness")
        if role == "required":
            if not isinstance(directness, str) or directness not in {"direct", "indirect"}:
                raise ValueError(
                    f"reference[{index}] role required needs directness direct or indirect"
                )
        elif directness is not None:
            raise ValueError(f"reference[{index}] non-required role cannot have directness")

        provenance = row.get("provenance")
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError(f"reference[{index}] needs a non-empty provenance string")

        pair = (prerequisite, target)
        if pair in annotations:
            raise ValueError(f"Duplicate reference annotation: {prerequisite} -> {target}")
        annotations[pair] = {
            "role": role,
            "directness": directness,
            "provenance": provenance.strip(),
        }
    return annotations


def _normalize_verdict(row: Mapping[str, Any], where: str) -> dict[str, Any]:
    supplied = [key for key in ("verdict", "accept", "label") if key in row]
    if len(supplied) != 1:
        raise ValueError(f"{where} needs exactly one of verdict, accept, or label")
    key = supplied[0]
    value = row[key]
    if key == "accept":
        if type(value) is not bool:
            raise ValueError(f"{where}.accept must be boolean")
        value = "accept" if value else "reject"
    if type(value) is bool:
        value = "accept" if value else "reject"
    if not isinstance(value, str):
        raise ValueError(f"Invalid verdict in {where}: {value!r}")

    directness = row.get("directness")
    if value == "required":
        if not isinstance(directness, str) or directness not in {"direct", "indirect"}:
            raise ValueError(f"{where} verdict required needs directness direct or indirect")
        value = f"required_{directness}"
    elif value in {"required_direct", "required_indirect"}:
        expected = value.removeprefix("required_")
        if directness is not None and directness != expected:
            raise ValueError(f"{where} verdict conflicts with directness")
    elif directness is not None:
        raise ValueError(f"{where} non-required verdict cannot have directness")

    if value in {"accept", "required_direct"}:
        return {"verdict": value, "required": True, "direct": True}
    if value == "required_indirect":
        return {"verdict": value, "required": True, "direct": False}
    if value in {"reject", "not_required"} | SUPPORT_ROLES | {"related_or_none"}:
        return {"verdict": value, "required": False, "direct": False}
    if value == "uncertain":
        return {"verdict": "abstain", "required": None, "direct": None}
    raise ValueError(f"Invalid verdict in {where}: {value!r}")


def _parse_predictions(
    raw: Any, endpoint_ids: set[str]
) -> tuple[str, dict[tuple[str, str], dict[str, Any]]]:
    if raw is None:
        raw = {"verdicts": []}
    if isinstance(raw, list):
        mode, rows = "verdicts", raw
    else:
        container = _mapping(raw, "predictions")
        has_verdicts = "verdicts" in container
        has_edges = "depends_on" in container
        if has_verdicts == has_edges:
            if not container:
                mode, rows = "verdicts", []
            else:
                raise ValueError("predictions must contain exactly one of verdicts or depends_on")
        elif has_verdicts:
            mode, rows = "verdicts", container["verdicts"]
        else:
            mode, rows = "depends_on", container["depends_on"]

    predictions: dict[tuple[str, str], dict[str, Any]] = {}
    if mode == "depends_on":
        edges = _parse_depends_on_edges(rows, "predictions.depends_on", endpoint_ids)
        return mode, {
            edge: {"verdict": "accept", "required": True, "direct": True}
            for edge in edges
        }

    for index, item in enumerate(_sequence(rows, "predictions.verdicts")):
        row = _mapping(item, f"predictions.verdicts[{index}]")
        edge = depends_on_to_prerequisite_of(row)
        for endpoint_id in edge:
            if endpoint_id not in endpoint_ids:
                raise ValueError(
                    f"Unknown endpoint {endpoint_id!r} in predictions.verdicts[{index}]"
                )
        if edge in predictions:
            raise ValueError(f"Duplicate prediction verdict: {edge[0]} -> {edge[1]}")
        predictions[edge] = _normalize_verdict(row, f"predictions.verdicts[{index}]")
    return mode, predictions


def _metrics(tp: int, fp: int, fn: int, scope: str) -> dict[str, Any]:
    precision_denominator = tp + fp
    recall_denominator = tp + fn
    f1_denominator = 2 * tp + fp + fn
    precision = tp / precision_denominator if precision_denominator else None
    recall = tp / recall_denominator if recall_denominator else None
    f1 = 2 * tp / f1_denominator if f1_denominator else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "precision_denominator": precision_denominator,
        "recall": recall,
        "recall_denominator": recall_denominator,
        "f1": f1,
        "f1_denominator": f1_denominator,
        "scope": scope,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _strong_components(
    nodes: set[str], adjacency: Mapping[str, set[str]]
) -> list[list[str]]:
    graph = _nx.DiGraph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from((source, target) for source in nodes for target in adjacency[source])
    return [sorted(component) for component in _nx.strongly_connected_components(graph)]


def _weak_components(nodes: set[str], adjacency: Mapping[str, set[str]]) -> list[list[str]]:
    undirected = {node: set(adjacency[node]) for node in nodes}
    for source, targets in adjacency.items():
        for target in targets:
            undirected[target].add(source)
    graph = _nx.Graph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from((source, target) for source in nodes for target in undirected[source])
    return [sorted(component) for component in _nx.connected_components(graph)]


def graph_diagnostics(
    endpoints: Iterable[str], prerequisite_of_edges: Iterable[tuple[str, str]]
) -> dict[str, Any]:
    """Describe a directed graph; edge tuples are prerequisite -> target.

    Unlike ``score_evaluation``, this low-level helper reports rather than raises
    on unknown edge endpoints, making invalid-endpoint diagnostics inspectable.
    """
    nodes = set(endpoints)
    raw_edges = list(prerequisite_of_edges)
    invalid = [edge for edge in raw_edges if edge[0] not in nodes or edge[1] not in nodes]
    valid = [edge for edge in raw_edges if edge[0] in nodes and edge[1] in nodes]
    unique_edges = set(valid)
    adjacency = {node: set() for node in nodes}
    reverse = {node: set() for node in nodes}
    for source, target in unique_edges:
        adjacency[source].add(target)
        reverse[target].add(source)

    self_loops = sorted((source, target) for source, target in valid if source == target)
    reciprocal = sorted(
        (left, right)
        for left, right in {
            tuple(sorted((source, target)))
            for source, target in unique_edges
            if source != target and (target, source) in unique_edges
        }
    )
    components = _strong_components(nodes, adjacency)
    nontrivial_sccs = sorted(
        (component for component in components if len(component) > 1),
        key=lambda component: tuple(component),
    )
    weak = sorted(_weak_components(nodes, adjacency), key=lambda component: tuple(component))
    isolated = sorted(node for node in nodes if not adjacency[node] and not reverse[node])
    largest = max((len(component) for component in weak), default=0)
    directed = _nx.DiGraph()
    directed.add_nodes_from(nodes)
    directed.add_edges_from(unique_edges)
    is_dag = _nx.is_directed_acyclic_graph(directed)
    reduced_edges = set(_nx.transitive_reduction(directed).edges()) if is_dag else None

    def edge_records(edges: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
        return [
            {"prerequisite_ku": source, "target_ku": target}
            for source, target in sorted(edges)
        ]

    return {
        "node_count": len(nodes),
        "edge_count": len(raw_edges),
        "unique_valid_edge_count": len(unique_edges),
        "duplicate_edge_count": len(valid) - len(unique_edges),
        "invalid_endpoint_edge_count": len(invalid),
        "invalid_endpoint_edges": [list(edge) for edge in invalid],
        "self_loop_count": len(self_loops),
        "self_loops": edge_records(self_loops),
        "reciprocal_pair_count": len(reciprocal),
        "reciprocal_pairs": [list(pair) for pair in reciprocal],
        "strongly_connected_component_count": len(components),
        "nontrivial_scc_count": len(nontrivial_sccs),
        "nontrivial_sccs": nontrivial_sccs,
        "nodes_in_nontrivial_scc_count": len({node for part in nontrivial_sccs for node in part}),
        "isolated_node_count": len(isolated),
        "isolated_node_ratio": _ratio(len(isolated), len(nodes)),
        "isolated_nodes": isolated,
        "weak_component_count": len(weak),
        "weak_components": weak,
        "largest_weak_component_size": largest,
        "largest_weak_component_ratio": _ratio(largest, len(nodes)),
        "component_algorithm": "networkx",
        "is_dag": is_dag,
        "transitive_reduction_edge_count": len(reduced_edges) if reduced_edges is not None else None,
        "structurally_redundant_edge_count": len(unique_edges - reduced_edges) if reduced_edges is not None else None,
        "transitive_reduction_note": "Diagnostic only, original predictions scored unchanged; structural redundancy does not prove semantic redundancy.",
    }


def transitive_closure(
    endpoints: Iterable[str], prerequisite_of_edges: Iterable[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Return non-reflexive directed reachability, including paths of length one."""
    nodes = set(endpoints)
    adjacency = {node: set() for node in nodes}
    for source, target in prerequisite_of_edges:
        if source in nodes and target in nodes:
            adjacency[source].add(target)
    reachable: set[tuple[str, str]] = set()
    for source in nodes:
        visited = set()
        pending = list(adjacency[source])
        while pending:
            target = pending.pop()
            if target in visited:
                continue
            visited.add(target)
            if target != source:
                reachable.add((source, target))
            pending.extend(adjacency[target] - visited)
    return reachable


def _relation_record(pair: tuple[str, str]) -> dict[str, str]:
    return {"prerequisite_ku": pair[0], "target_ku": pair[1]}


def _gold_class(annotation: Mapping[str, Any]) -> str:
    if annotation["role"] == "required":
        return f"required_{annotation['directness']}"
    return annotation["role"]


def score_evaluation(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and score one finite KU prerequisite evaluation without mutation."""
    root = _mapping(data, "input")
    if "endpoints" not in root:
        raise ValueError("input needs endpoints")
    endpoint_ids = _endpoint_ids(root["endpoints"])
    candidate_edges = _parse_depends_on_edges(root.get("candidates", []), "candidates", endpoint_ids)
    reference = _parse_reference(root.get("reference", []), endpoint_ids)
    prediction_mode, predictions = _parse_predictions(root.get("predictions"), endpoint_ids)

    determinate = {pair: row for pair, row in reference.items() if row["role"] != "uncertain"}
    determinate_pairs = set(determinate)
    direct_gold = {
        pair
        for pair, row in determinate.items()
        if row["role"] == "required" and row["directness"] == "direct"
    }
    required_gold = {pair for pair, row in determinate.items() if row["role"] == "required"}
    indirect_gold = {
        pair
        for pair, row in determinate.items()
        if row["role"] == "required" and row["directness"] == "indirect"
    }
    support_gold = {
        pair for pair, row in determinate.items() if row["role"] in SUPPORT_ROLES
    }

    # A depends_on list is a complete accepted graph; omitted candidates imply rejection.
    candidate_verdicts = {
        pair: predictions[pair]
        for pair in candidate_edges
        if pair in predictions
    }
    if prediction_mode == "depends_on":
        candidate_verdicts.update(
            {
                pair: {"verdict": "reject", "required": False, "direct": False}
                for pair in candidate_edges - set(predictions)
            }
        )
    candidate_labeled = candidate_edges & determinate_pairs
    candidate_reviewed = {
        pair: candidate_verdicts[pair]
        for pair in candidate_labeled
        if pair in candidate_verdicts
    }
    candidate_scored = {
        pair: verdict
        for pair, verdict in candidate_reviewed.items()
        if verdict["required"] is not None
    }

    candidate_direct_hits = len(candidate_edges & direct_gold)
    candidate_recall = {
        "hits": candidate_direct_hits,
        "denominator": len(direct_gold),
        "recall": _ratio(candidate_direct_hits, len(direct_gold)),
        "scope": "Gold required-direct pairs present in the raw candidate pool",
    }

    pool_required_predicted = {
        pair for pair, verdict in candidate_scored.items() if verdict["required"]
    }
    pool_direct_predicted = {
        pair for pair, verdict in candidate_scored.items() if verdict["direct"]
    }
    candidate_pool_requiredness = _metrics(
        len(pool_required_predicted & required_gold),
        len(pool_required_predicted - required_gold),
        len((candidate_scored.keys() & required_gold) - pool_required_predicted),
        "Only candidate pairs with a determinate reference and a non-abstaining verdict; positive means required direct or indirect.",
    )
    candidate_pool_directness = _metrics(
        len(pool_direct_predicted & direct_gold),
        len(pool_direct_predicted - direct_gold),
        len((candidate_scored.keys() & direct_gold) - pool_direct_predicted),
        "Same conditional candidate review subset; positive means required and direct.",
    )

    role_confusion: dict[str, dict[str, int]] = {}
    for pair, verdict in candidate_reviewed.items():
        gold_label = _gold_class(determinate[pair])
        predicted_label = verdict["verdict"]
        role_confusion.setdefault(gold_label, {})[predicted_label] = (
            role_confusion.setdefault(gold_label, {}).get(predicted_label, 0) + 1
        )

    predicted_direct = {
        pair for pair, verdict in predictions.items() if verdict["direct"] is True
    }
    predicted_required = {
        pair for pair, verdict in predictions.items() if verdict["required"] is True
    }
    scored_direct_predictions = predicted_direct & determinate_pairs
    end_to_end = _metrics(
        len(scored_direct_predictions & direct_gold),
        len(scored_direct_predictions - direct_gold),
        len(direct_gold - scored_direct_predictions),
        "Final predicted direct edges against determinate directed reference pairs; unlabeled and uncertain pairs are excluded, while missed Gold edges remain false negatives.",
    )

    indirect_false_accept_pairs = sorted(indirect_gold & predicted_direct)
    indirect_false_accepts = {
        "count": len(indirect_false_accept_pairs),
        "denominator": len(indirect_gold),
        "rate": _ratio(len(indirect_false_accept_pairs), len(indirect_gold)),
        "pairs": [_relation_record(pair) for pair in indirect_false_accept_pairs],
        "scope": "Gold required-indirect pairs emitted as direct required predictions",
    }
    support_by_role = {}
    for role in sorted(SUPPORT_ROLES):
        pairs = sorted(
            pair
            for pair in support_gold & predicted_required
            if determinate[pair]["role"] == role
        )
        total = sum(row["role"] == role for row in determinate.values())
        support_by_role[role] = {
            "count": len(pairs),
            "denominator": total,
            "pairs": [_relation_record(pair) for pair in pairs],
        }
    support_false_accept_pairs = sorted(support_gold & predicted_required)
    support_total = sum(row["role"] in SUPPORT_ROLES for row in determinate.values())
    support_false_accepts = {
        "count": len(support_false_accept_pairs),
        "denominator": support_total,
        "rate": _ratio(len(support_false_accept_pairs), support_total),
        "by_role": support_by_role,
        "pairs": [_relation_record(pair) for pair in support_false_accept_pairs],
        "scope": "Gold principle_support or proof_support pairs emitted as required predictions",
    }

    gold_required_graph = set(required_gold)
    gold_closure = transitive_closure(endpoint_ids, gold_required_graph)
    predicted_closure = transitive_closure(endpoint_ids, predicted_required)
    closure_gold = gold_closure & determinate_pairs
    closure_predicted = predicted_closure & determinate_pairs
    closure_metrics = _metrics(
        len(closure_gold & closure_predicted),
        len(closure_predicted - closure_gold),
        len(closure_gold - closure_predicted),
        "Non-reflexive transitive reachability evaluated only on explicitly determinate directed reference pairs. Gold closure uses required direct and indirect annotations only; uncertain edges are excluded.",
    )

    provenance_counts = Counter(row["provenance"] for row in reference.values())
    provenance_kinds = Counter(
        "human" if "human" in label.lower() else "draft" if "draft" in label.lower() else "other"
        for label in (row["provenance"] for row in reference.values())
    )
    candidate_diagnostics = graph_diagnostics(endpoint_ids, candidate_edges)
    prediction_diagnostics = graph_diagnostics(endpoint_ids, predicted_required)
    reference_diagnostics = graph_diagnostics(endpoint_ids, gold_required_graph)

    return {
        "schema_version": 1,
        "scope": {
            "endpoint_count": len(endpoint_ids),
            "candidate_edge_count": len(candidate_edges),
            "prediction_mode": prediction_mode,
            "prediction_verdict_count": len(predictions),
            "reference_annotation_count": len(reference),
            "determinate_reference_pair_count": len(determinate_pairs),
            "uncertain_reference_pair_count": len(reference) - len(determinate_pairs),
            "candidate_pairs_with_determinate_labels": len(candidate_labeled),
            "candidate_pairs_reviewed": len(candidate_reviewed),
            "candidate_pairs_scored": len(candidate_scored),
            "candidate_verdicts_missing": len(candidate_labeled - set(candidate_reviewed)),
            "candidate_verdict_abstentions": sum(
                verdict["required"] is None for verdict in candidate_reviewed.values()
            ),
            "reference_provenance_counts": dict(sorted(provenance_counts.items())),
            "reference_provenance_kinds": dict(sorted(provenance_kinds.items())),
        },
        "candidate_direct_recall": candidate_recall,
        "candidate_pool_review": {
            "requiredness": candidate_pool_requiredness,
            "directness": candidate_pool_directness,
            "gold_role_by_predicted_verdict": {
                label: dict(sorted(columns.items()))
                for label, columns in sorted(role_confusion.items())
            },
        },
        "end_to_end_direct": end_to_end,
        "indirect_false_accepts": indirect_false_accepts,
        "support_false_accepts": support_false_accepts,
        "reachability": {
            "metrics": closure_metrics,
            "gold_closure_edge_count": len(gold_closure),
            "predicted_closure_edge_count": len(predicted_closure),
            "scored_gold_closure_edge_count": len(closure_gold),
            "scored_predicted_closure_edge_count": len(closure_predicted),
            "unlabeled_predicted_closure_edge_count": len(predicted_closure - determinate_pairs),
            "gold_closure_uses_uncertain_edges": False,
        },
        "graph_diagnostics": {
            "candidates": candidate_diagnostics,
            "predicted_required_graph": prediction_diagnostics,
            "reference_required_graph": reference_diagnostics,
        },
        "caveat": (
            "Draft-labeled metrics are agreement with provisional annotations, not expert-Gold accuracy. "
            "Human provenance alone does not establish expert adjudication. Unlabeled and uncertain pairs "
            "are not negative examples; all reported scores are descriptive for this finite endpoint set."
        ),
    }


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="finite prerequisite evaluation JSON")
    parser.add_argument("-o", "--output", type=Path, help="write report JSON here (default: stdout)")
    args = parser.parse_args(argv)
    try:
        result = score_evaluation(_read_json(args.input))
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            if args.input.resolve() == args.output.resolve():
                raise ValueError("output path must not overwrite the input")
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
        else:
            print(rendered, end="")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
