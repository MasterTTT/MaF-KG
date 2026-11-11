"""Validate and consolidate the explicitly reviewed prospective graph artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = ROOT / "evaluation" / "prospective_2026-10-04"
OUTPUT_JSON = EVALUATION_ROOT / "quality_review_summary.json"
OUTPUT_MD = EVALUATION_ROOT / "quality_review_summary.md"

REVIEW_INPUTS = (
    ("taylor_original", "preliminary_taylor_review/review.json"),
    ("graph_local", "preliminary_taylor_review/luna_sequential/review.json"),
    ("graph_local", "preliminary_taylor_review/mimo_sequential/review.json"),
    ("graph_local", "preliminary_taylor_review/kimi_direct/review.json"),
    ("multi_graph", "integration_review/review.json"),
    ("multi_graph", "integration_review/additional_completed/review.json"),
    ("graph_local", "ode_review/deepseek_direct/review.json"),
    ("ode_bundle", "ode_review/remaining_completed/review.json"),
    ("graph_local", "ode_review/kimi_sequential/review.json"),
)

MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek",
    "gpt-6-luna": "gpt6",
    "mimo-v2.6-flash": "mimo",
    "kimi-k2.6": "kimi",
}
STRICT_STATUSES = {"matched", "strict_type_match"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str | Path, root: Path = ROOT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=True)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def graph_coordinates(value: str | Path) -> tuple[str, str, str, str]:
    parts = PurePosixPath(str(value).replace("\\", "/")).parts
    run_index = next(
        (index for index, part in enumerate(parts) if part.startswith("multimodel_run")),
        None,
    )
    require(
        run_index is not None and len(parts) > run_index + 4,
        f"Cannot derive run/model/section/arm from graph path: {value}",
    )
    run_name, model, section, arm, filename = parts[run_index : run_index + 5]
    require(filename == "graph.json", f"Not a graph.json path: {value}")
    require(model in MODEL_ALIASES, f"Unexpected reviewed model in graph path: {model}")
    require(arm in {"direct", "sequential"}, f"Unexpected graph arm: {arm}")
    return run_name, model, section, arm


def graph_alias(value: str | Path, kind: str) -> str:
    run_name, model, _section, arm = graph_coordinates(value)
    run_label = run_name.removeprefix("multimodel_")
    model_alias = MODEL_ALIASES[model]
    if kind == "multi_graph" and model == "gpt-6-luna":
        model_alias = "luna"
    return f"{run_label}_{model_alias}_{arm}"


def _binding_rows(review: dict[str, Any]) -> list[dict[str, Any]]:
    scope = review.get("scope", {})
    bindings = (
        review.get("sha256_bindings")
        or review.get("shared_sha256_bindings")
        or scope.get("sha256_bindings")
        or scope.get("shared_sha256_bindings")
    )
    require(isinstance(bindings, list) and bindings, "Review has no SHA-256 bindings")
    return bindings


def verify_bindings(
    review: dict[str, Any], graph_paths: list[Path], root: Path = ROOT
) -> tuple[dict[Path, str], Path, Path, Path, dict[str, Any]]:
    scope = review["scope"]
    bound: dict[Path, str] = {}
    role_paths: dict[str, Path] = {}
    for row in _binding_rows(review):
        path = resolve_path(row["path"], root)
        claimed = row["sha256"]
        role_paths[row.get("role", "").lower()] = path
        if row.get("role", "").lower().endswith("manifest") or path.name.lower() == "manifest.json":
            continue
        require(
            sha256_file(path) == claimed,
            f"SHA-256 mismatch for immutable review binding: {row['path']}",
        )
        if path in bound:
            require(bound[path] == claimed, f"Conflicting SHA-256 bindings for {path}")
        bound[path] = claimed

    reference_value = scope.get("reference_path") or next(
        (str(path) for role, path in role_paths.items() if "reference" in role), None
    )
    source_value = scope.get("source_path") or next(
        (str(path) for role, path in role_paths.items() if "source" in role), None
    )
    require(reference_value is not None, "Review has no bound frozen reference path")
    require(source_value is not None, "Review has no bound actual source path")
    reference_path = resolve_path(reference_value, root)
    source_path = resolve_path(source_value, root)
    require(reference_path in bound, "Frozen reference is not SHA-256 bound")
    require(source_path in bound, "Actual source is not SHA-256 bound")
    reference = read_json(reference_path)
    sections = [row for row in reference.get("sections", []) if row.get("id") == scope["section_id"]]
    require(len(sections) == 1, "Frozen reference must contain one reviewed section")
    ref_section = sections[0]
    require(
        ref_section.get("source_sha256") == bound[source_path],
        "Bound source hash differs from frozen reference section source hash",
    )
    if ref_section.get("source"):
        require(
            resolve_path(ref_section["source"], root) == source_path,
            "Bound source path differs from frozen reference section source path",
        )
    if scope.get("source_lines_inclusive") is not None:
        require(
            scope["source_lines_inclusive"]
            == [ref_section.get("start_line"), ref_section.get("end_line")],
            "Review source line range differs from frozen reference",
        )
    for graph_path in graph_paths:
        if graph_path in bound:
            continue
        # The ODE bundle binds each graph in its graph_reviews records instead.
        require(
            any(
                Path(row.get("path", "")).name == "graph.json"
                and resolve_path(row["path"], root) == graph_path
                for row in _binding_rows(review)
                if row.get("path")
            )
            or review.get("graph_reviews"),
            f"Reviewed graph has no immutable SHA-256 binding: {graph_path}",
        )
    return bound, reference_path, source_path, reference


def _single_graph_path(review: dict[str, Any]) -> str:
    scope = review["scope"]
    resolution = scope.get("manifest_resolution", {})
    for value in (
        scope.get("graph_path"),
        resolution.get("graph_path"),
        scope.get("reviewed_graph_only"),
    ):
        if value:
            return value
    raise ValueError("Graph-local review does not identify its graph path")


def _identity_rows_for(review: dict[str, Any], kind: str, alias: str | None, graph_review: dict[str, Any] | None) -> list[dict[str, Any]]:
    if kind == "taylor_original":
        return [
            {"item_id": row["id"], **row["annotations"][alias]}
            for row in review["mandatory_items"]
        ]
    if kind == "ode_bundle":
        return graph_review["mandatory_typed_identity_alignments"]
    rows = review["mandatory_item_annotations"]
    if kind == "multi_graph":
        return [
            {"item_id": row["item_id"], **row["annotations"][alias]}
            for row in rows
        ]
    return rows


def _relation_rows_for(review: dict[str, Any], kind: str, alias: str | None, graph_review: dict[str, Any] | None) -> list[dict[str, Any]]:
    if kind == "taylor_original":
        return [
            {"reference_id": row["reference_id"], **row["strict_checks"][alias]}
            for row in review["required_relations"]
        ]
    if kind == "ode_bundle":
        reference_edges = {row["reference_id"]: row for row in review["finite_reference_edges"]}
        return [
            {**reference_edges[row["reference_id"]], **row}
            for row in graph_review["finite_relation_results"]
        ]
    rows = review["finite_reference_relations"]
    if kind == "multi_graph":
        return [
            {
                key: value
                for key, value in row.items()
                if key not in {"annotations", "strict_endpoint_ids"}
            }
            | row["annotations"][alias]
            for row in rows
        ]
    return rows


def _graph_specs(review: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    scope = review["scope"]
    if kind == "ode_bundle":
        return [
            {
                "graph_path": row["graph_path"],
                "alias": None,
                "graph_review": row,
            }
            for row in review["graph_reviews"]
        ]
    if kind in {"taylor_original", "multi_graph"}:
        paths = scope["reviewed_graphs_only"]
        return [
            {"graph_path": value, "alias": graph_alias(value, kind), "graph_review": None}
            for value in paths
        ]
    return [{"graph_path": _single_graph_path(review), "alias": None, "graph_review": None}]


def _graph_hash_claims(review: dict[str, Any], spec: dict[str, Any], graph_path: Path, bindings: dict[Path, str]) -> list[str]:
    claims: list[str] = []
    graph_review = spec.get("graph_review") or {}
    for value in (
        graph_review.get("graph_sha256"),
        (graph_review.get("manifest_job") or {}).get("graph_sha256"),
        graph_review.get("manifest_graph_sha256"),
        review.get("scope", {}).get("manifest_resolution", {}).get("manifest_graph_sha256"),
    ):
        if value:
            claims.append(value)
    target_rows = review.get("scope", {}).get("target_job_resolution", [])
    if isinstance(target_rows, dict):
        target_rows = [target_rows]
    for row in target_rows:
        coords = graph_coordinates(graph_path)
        if (
            row.get("model", coords[1]) == coords[1]
            and row.get("mode", coords[3]) == coords[3]
            and row.get("manifest_graph_sha256")
        ):
            claims.append(row["manifest_graph_sha256"])
    for row in review.get("sha256_bindings", []) + review.get("shared_sha256_bindings", []):
        if row.get("path") and resolve_path(row["path"]) == graph_path:
            claims.append(row["sha256"])
    for row in review.get("scope", {}).get("sha256_bindings", []):
        if row.get("path") and resolve_path(row["path"]) == graph_path:
            claims.append(row["sha256"])
    if graph_path in bindings:
        claims.append(bindings[graph_path])
    require(bool(claims), f"Review has no SHA-256 claim for graph: {graph_path}")
    require(len(set(claims)) == 1, f"Review has conflicting graph SHA-256 claims: {graph_path}")
    return claims


def _manifest_path(review: dict[str, Any], graph_path: Path, root: Path) -> Path:
    scope = review["scope"]
    resolution = scope.get("manifest_resolution", {})
    if resolution.get("manifest_path"):
        return resolve_path(resolution["manifest_path"], root)
    run_value = scope.get("requested_run") or scope.get("run")
    if run_value:
        candidate = Path(run_value)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.suffix.lower() == ".json":
            return candidate.resolve(strict=True)
        return (candidate / "manifest.json").resolve(strict=True)
    return (graph_path.parents[3] / "manifest.json").resolve(strict=True)


def _manifest_claims(review: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    claims = []
    scope_resolution = review.get("scope", {}).get("manifest_resolution", {})
    for key in ("target_job_status", "job_status"):
        if scope_resolution.get(key) is not None:
            require(scope_resolution[key] == "completed", "Review target job is not marked completed")
    for key in ("target_job_exit_code", "exit_code"):
        if scope_resolution.get(key) is not None:
            require(scope_resolution[key] == 0, "Review target job is not marked zero-exit")
    graph_review = spec.get("graph_review") or {}
    if graph_review.get("manifest_job"):
        claims.append(graph_review["manifest_job"])
    if graph_review.get("target_job_resolution"):
        claims.append(graph_review["target_job_resolution"])
    target_rows = review.get("scope", {}).get("target_job_resolution", [])
    if isinstance(target_rows, dict):
        target_rows = [target_rows]
    for row in target_rows:
        coords = graph_coordinates(spec["graph_path"])
        if row.get("model") == coords[1] and row.get("mode") == coords[3]:
            claims.append(row)
    if any(claims):
        for claim in claims:
            status = claim.get("status", claim.get("manifest_job_status"))
            exit_code = claim.get("exit_code")
            if status is not None:
                require(status == "completed", "Review target manifest job is not completed")
            if exit_code is not None:
                require(exit_code == 0, "Review target manifest job is not zero-exit")
    return claims


def verify_manifest_job(
    review: dict[str, Any], spec: dict[str, Any], graph_path: Path, graph_sha: str, root: Path = ROOT
) -> tuple[Path, dict[str, Any]]:
    manifest_path = _manifest_path(review, graph_path, root)
    manifest = read_json(manifest_path)
    _run, model, section, arm = graph_coordinates(graph_path)
    jobs = [
        row for row in manifest.get("jobs", [])
        if row.get("model") == model and row.get("section") == section and row.get("name") == arm
    ]
    require(len(jobs) == 1, f"Expected one manifest row for {model}/{section}/{arm}")
    job = jobs[0]
    require(job.get("status") == "completed", f"Manifest job is not completed: {model}/{section}/{arm}")
    require(job.get("exit_code") == 0, f"Manifest job is not zero-exit: {model}/{section}/{arm}")
    require(job.get("graph_sha256") == graph_sha, f"Graph hash differs from manifest: {graph_path}")
    if job.get("directory"):
        job_graph = (resolve_path(job["directory"], root) / "graph.json")
        require(job_graph == graph_path, f"Manifest graph path differs from review graph: {graph_path}")
    for claim in _manifest_claims(review, spec):
        if claim.get("graph_sha256"):
            require(claim["graph_sha256"] == graph_sha, "Review's manifest graph hash differs from graph")
        if claim.get("manifest_graph_sha256"):
            require(claim["manifest_graph_sha256"] == graph_sha, "Review's manifest graph hash differs from graph")
    return manifest_path, job


def _candidate_edge(row: dict[str, Any]) -> Any:
    for key in ("predicted_relation", "nearest_predicted_edge", "predicted_edge"):
        if row.get(key) is not None:
            return row[key]
    return row.get("predicted_relation_id")


def _endpoint_claim(row: dict[str, Any]) -> tuple[Any, Any] | None:
    if row.get("source_identity_id") is not None or row.get("target_identity_id") is not None:
        return row.get("source_identity_id"), row.get("target_identity_id")
    endpoints = row.get("strict_endpoint_ids")
    if isinstance(endpoints, dict):
        return endpoints.get("source"), endpoints.get("target")
    return None


def score_graph(
    reference: dict[str, Any],
    graph: dict[str, Any],
    section: str,
    identity_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = graph.get("nodes", [])
    node_by_id = {row["id"]: row for row in nodes}
    require(len(node_by_id) == len(nodes), "Graph contains duplicate node IDs")
    relations = graph.get("relations", [])
    relation_by_id = {row["id"]: row for row in relations}
    require(len(relation_by_id) == len(relations), "Graph contains duplicate relation IDs")
    for edge in relations:
        require(
            edge.get("source_id") in node_by_id and edge.get("target_id") in node_by_id,
            "Graph relation references a missing node",
        )
    triples = {
        (edge["source_id"], edge["type"], edge["target_id"])
        for edge in relations
    }

    mandatory = [
        row for row in reference.get("nodes", [])
        if row.get("section") == section and row.get("status") == "M"
    ]
    mandatory_by_id = {row["id"]: row for row in mandatory}
    require(len(mandatory_by_id) == len(mandatory), "Frozen reference has duplicate mandatory node IDs")
    annotated = {row["item_id"]: row for row in identity_rows}
    require(
        len(annotated) == len(identity_rows) and set(annotated) == set(mandatory_by_id),
        "Review identity alignments do not exactly cover frozen mandatory nodes",
    )

    candidate_ids: dict[str, str | None] = {}
    strict_ids: dict[str, str | None] = {}
    identity_results = []
    strict_used: set[str] = set()
    alignment_discrepancies = []
    for item_id, ref_node in mandatory_by_id.items():
        row = annotated[item_id]
        for key, expected in (("reference_type", ref_node["type"]), ("type", ref_node["type"]), ("reference_name", ref_node["name"])):
            if key in row and row[key] is not None:
                require(row[key] == expected, f"Review reference metadata mismatch for {item_id}: {key}")
        if "checklist" in row:
            require(row["checklist"] == ref_node.get("checklist", []), f"Review checklist differs for {item_id}")
        for content_id in row.get("content_ids", []) or []:
            require(content_id in node_by_id, f"Review lists unknown content carrier {content_id!r}")

        identity_id = row.get("identity_id")
        status = row.get("identity_status")
        if identity_id is not None:
            require(identity_id in node_by_id, f"Review identity {identity_id!r} is absent from graph")
            candidate_ids[item_id] = identity_id
            actual_type = node_by_id[identity_id].get("type")
            if "content_ids" in row and row["content_ids"] is not None:
                require(identity_id in row["content_ids"], f"Review identity is not a listed carrier for {item_id}")
        else:
            candidate_ids[item_id] = None
            actual_type = None
        declared_graph_type = row.get("graph_type", row.get("identity_type"))
        if "graph_type" in row or "identity_type" in row:
            require(declared_graph_type == actual_type, f"Review graph type differs for {item_id}")

        actual_type_hit = identity_id is not None and actual_type == ref_node["type"]
        declared_type_hit = row.get("exact_type_hit")
        if declared_type_hit is not None:
            require(isinstance(declared_type_hit, bool), f"Invalid exact_type_hit for {item_id}")
            if declared_type_hit != actual_type_hit:
                alignment_discrepancies.append({
                    "item_id": item_id,
                    "kind": "declared_type_hit_differs_from_graph_type",
                    "declared": declared_type_hit,
                    "recomputed": actual_type_hit,
                })
            alignment_is_strict = declared_type_hit
        else:
            alignment_is_strict = status in STRICT_STATUSES

        hit = bool(alignment_is_strict and actual_type_hit)
        if status in STRICT_STATUSES:
            require(identity_id is not None, f"Strict identity status has no identity ID for {item_id}")
            require(actual_type_hit, f"Strict identity alignment has wrong graph type for {item_id}")
        if hit:
            require(identity_id not in strict_used, "Strict mandatory identity mappings are not one-to-one")
            strict_used.add(identity_id)
            strict_ids[item_id] = identity_id
        else:
            strict_ids[item_id] = None
        identity_results.append({
            "item_id": item_id,
            "reference_type": ref_node["type"],
            "identity_id": identity_id,
            "graph_type": actual_type,
            "review_identity_status": status,
            "typed_identity_hit": hit,
        })

    reference_edges = [
        row for row in reference.get("relations_provisional", [])
        if row.get("section") == section
    ]
    reference_edge_by_id = {row["id"]: row for row in reference_edges}
    require(len(reference_edge_by_id) == len(reference_edges), "Frozen reference has duplicate finite relation IDs")
    annotated_edges = {row["reference_id"]: row for row in relation_rows}
    require(
        len(annotated_edges) == len(relation_rows)
        and set(annotated_edges) == set(reference_edge_by_id),
        "Review relation alignments do not exactly cover frozen finite relations",
    )

    relation_results = []
    relation_discrepancies = []
    for relation_id, ref_edge in reference_edge_by_id.items():
        row = annotated_edges[relation_id]
        expected_metadata = {
            "source_item": ref_edge["start"],
            "target_item": ref_edge["end"],
            "type": ref_edge["type"],
        }
        for key, value in expected_metadata.items():
            if key in row and row[key] is not None:
                require(row[key] == value, f"Review relation metadata mismatch for {relation_id}: {key}")
        source_item, target_item = ref_edge["start"], ref_edge["end"]
        require(source_item in mandatory_by_id and target_item in mandatory_by_id,
                f"Finite relation {relation_id} does not connect mandatory reference nodes")
        endpoint_claim = _endpoint_claim(row)
        if endpoint_claim is not None:
            require(
                endpoint_claim == (candidate_ids[source_item], candidate_ids[target_item]),
                f"Review strict endpoints differ from its identity alignments for {relation_id}",
            )
        source_id, target_id = strict_ids[source_item], strict_ids[target_item]
        hit = bool(
            source_id is not None
            and target_id is not None
            and (source_id, ref_edge["type"], target_id) in triples
        )
        claimed_hit = row.get("exact_match", row.get("strict_hit"))
        if claimed_hit is not None:
            require(isinstance(claimed_hit, bool), f"Invalid relation hit annotation for {relation_id}")
            if claimed_hit != hit:
                relation_discrepancies.append({
                    "reference_id": relation_id,
                    "kind": "declared_relation_hit_differs_from_graph_triple",
                    "declared": claimed_hit,
                    "recomputed": hit,
                })

        candidate = _candidate_edge(row)
        candidate_id = candidate if isinstance(candidate, str) else None
        if isinstance(candidate, dict):
            candidate_id = candidate.get("relation_id") or candidate.get("id")
        if candidate_id is not None:
            require(candidate_id in relation_by_id, f"Review candidate edge {candidate_id!r} is absent from graph")
            actual_candidate = relation_by_id[candidate_id]
            if isinstance(candidate, dict):
                for key, graph_key in (("source_id", "source_id"), ("type", "type"), ("target_id", "target_id")):
                    if candidate.get(key) is not None:
                        require(candidate[key] == actual_candidate.get(graph_key),
                                f"Review candidate edge differs from graph for {relation_id}")
        elif isinstance(candidate, dict) and all(key in candidate for key in ("source_id", "type", "target_id")):
            require(
                (candidate["source_id"], candidate["type"], candidate["target_id"]) in triples,
                f"Review candidate triple is absent from graph for {relation_id}",
            )

        relation_results.append({
            "reference_id": relation_id,
            "source_item": source_item,
            "type": ref_edge["type"],
            "target_item": target_item,
            "strict_source_id": source_id,
            "strict_target_id": target_id,
            "exact_graph_hit": hit,
        })

    return {
        "mandatory_typed_identity_hits": sum(row["typed_identity_hit"] for row in identity_results),
        "mandatory_typed_identity_total": len(mandatory),
        "exact_finite_relation_hits": sum(row["exact_graph_hit"] for row in relation_results),
        "exact_finite_relation_total": len(reference_edges),
        "identity_results": identity_results,
        "relation_results": relation_results,
        "alignment_discrepancies": alignment_discrepancies,
        "relation_discrepancies": relation_discrepancies,
    }


def validate_review_file(review_path: Path, kind: str, root: Path = ROOT) -> list[dict[str, Any]]:
    review = read_json(review_path)
    specs = _graph_specs(review, kind)
    scope = review["scope"]
    graph_paths = [resolve_path(spec["graph_path"], root) for spec in specs]
    bindings, reference_path, source_path, reference = verify_bindings(
        review, graph_paths, root
    )
    rows = []
    for spec, graph_path in zip(specs, graph_paths):
        run_name, model, section, arm = graph_coordinates(graph_path)
        require(section == scope["section_id"], f"Graph section differs from review scope: {graph_path}")
        if scope.get("model"):
            require(scope["model"] == model, f"Graph model differs from review scope: {graph_path}")
        if scope.get("mode"):
            require(scope["mode"] == arm, f"Graph arm differs from review scope: {graph_path}")
        graph = read_json(graph_path)
        graph_sha = sha256_file(graph_path)
        claims = _graph_hash_claims(review, spec, graph_path, bindings)
        require(claims[0] == graph_sha, f"Reviewed graph SHA-256 mismatch: {graph_path}")
        manifest_path, job = verify_manifest_job(review, spec, graph_path, graph_sha, root)
        graph_review = spec.get("graph_review")
        identity_rows = _identity_rows_for(review, kind, spec.get("alias"), graph_review)
        relation_rows = _relation_rows_for(review, kind, spec.get("alias"), graph_review)
        scored = score_graph(
            reference,
            graph,
            section,
            identity_rows,
            relation_rows,
        )
        rows.append({
            "model": model,
            "section": section,
            "arm": arm,
            "run": run_name,
            "review_status": "reviewed_completed",
            "job_status": job["status"],
            "job_exit_code": job["exit_code"],
            "reviewer": review.get("reviewer"),
            "review_type": review.get("review_type"),
            "review_provenance": "assistant-supported review; not independent human Gold",
            "review_path": str(review_path.resolve()),
            "graph_path": str(graph_path),
            "manifest_path": str(manifest_path),
            "graph_sha256": graph_sha,
            "reference_sha256": bindings[reference_path],
            "source_sha256": bindings[source_path],
            **scored,
        })
    return rows


def build_summary(root: Path = ROOT) -> dict[str, Any]:
    rows = []
    for kind, relative_path in REVIEW_INPUTS:
        rows.extend(validate_review_file(root / "evaluation" / "prospective_2026-10-04" / relative_path, kind, root))
    keys = [(row["model"], row["section"], row["arm"]) for row in rows]
    require(len(keys) == 19, f"Expected exactly 19 reviewed graph rows, found {len(keys)}")
    require(len(set(keys)) == len(keys), "Duplicate model/section/arm review rows")
    return {
        "schema_version": "prospective-quality-review-summary.v1",
        "status": "all 19 explicitly scoped review graphs validated against immutable bindings and frozen references",
        "provenance": {
            "reviewers": sorted({row["reviewer"] for row in rows}),
            "classification": "Assistant-supported reviews; not independent human annotation or human Gold.",
            "semantic_limits": "Self-containedness, checklist completeness, source support, and duplicate judgments are not included in these mechanically recomputed counts.",
        },
        "metric_definitions": {
            "typed_identity": "A review-aligned mandatory identity with an existing graph node of the exact frozen-reference type and an explicitly strict/matched alignment status.",
            "finite_exact_relation": "A directed (source_id, type, target_id) triple in the graph matching a finite frozen-reference relation between strict typed identity nodes.",
            "interpretation": "Hits/total counts only. No precision, F1, or universal model ranking is computed or implied.",
        },
        "review_scope_status": {
            "scope": "The 19 explicitly requested reviewed graph artifacts only; this is not a full run-manifest inventory.",
            "reviewed_completed": len(rows),
            "unreviewed": 0,
            "failed": 0,
            "non_scored": {"unreviewed": [], "failed": []},
            "not_scored_policy": "Unreviewed and failed jobs are separate non-scored states; neither is represented as a zero-quality row.",
        },
        "rows": rows,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Prospective Quality Review Summary",
        "",
        summary["status"] + ".",
        "",
        "Scope: only the 19 reviewed successful graphs. Failed rows below are review-scope counts, not experiment failure counts. The full run06 inventory has 30 attempted jobs: 19 completed and 11 failed; see multimodel_run06/operational_summary.md.",
        "",
        summary["provenance"]["classification"],
        "",
        summary["provenance"]["semantic_limits"],
        "",
        "The table reports strict typed identity hits and exact directed finite-reference relation hits only. Counts are not precision/F1 and do not establish a universal model ranking.",
        "",
        "| Model | Section | Arm | Typed identities | Exact finite relations | Graph SHA-256 |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in summary["rows"]:
        lines.append(
            f"| {row['model']} | {row['section']} | {row['arm']} | "
            f"{row['mandatory_typed_identity_hits']}/{row['mandatory_typed_identity_total']} | "
            f"{row['exact_finite_relation_hits']}/{row['exact_finite_relation_total']} | "
            f"`{row['graph_sha256']}` |"
        )
    lines.extend([
        "",
        f"Reviewed completed rows: {summary['review_scope_status']['reviewed_completed']}; ",
        f"unreviewed rows: {summary['review_scope_status']['unreviewed']}; ",
        f"failed rows: {summary['review_scope_status']['failed']}.",
        "",
        "Hashes were checked against each review's immutable bindings and the corresponding completed manifest job. Counts were recomputed from the frozen reference, review identity alignments, and actual graph triples; declared summary totals were not used.",
        "",
    ])
    return "\n".join(lines)


def write_summary(output_dir: Path = EVALUATION_ROOT, root: Path = ROOT) -> dict[str, Any]:
    summary = build_summary(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "quality_review_summary.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with (output_dir / "quality_review_summary.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_JSON.parent)
    args = parser.parse_args()
    summary = write_summary(args.output_dir)
    print(json.dumps({"reviewed_completed": len(summary["rows"]), "output_dir": str(args.output_dir)}, indent=2))


if __name__ == "__main__":
    main()
