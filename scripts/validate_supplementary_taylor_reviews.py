"""Mechanically validate supplementary Taylor review bindings and counts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = (
    ROOT
    / "evaluation"
    / "prospective_2026-10-04"
    / "preliminary_taylor_review"
)
REVIEW_DIRS = (
    ("luna_sequential", REVIEW_ROOT / "luna_sequential"),
    ("mimo_sequential", REVIEW_ROOT / "mimo_sequential"),
    ("kimi_direct", REVIEW_ROOT / "kimi_direct"),
)
SUMMARY_JSON = REVIEW_ROOT / "supplementary_validated_summary.json"
SUMMARY_MD = REVIEW_ROOT / "supplementary_validated_summary.md"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve(strict=True)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _triplet(relation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        relation["source_id"],
        relation["type"],
        relation["target_id"],
    )


def _validate_bindings(review: dict[str, Any]) -> dict[str, Any]:
    scope = review["scope"]
    resolution = scope["manifest_resolution"]
    manifest_path = resolve_path(resolution["manifest_path"])
    manifest = read_json(manifest_path)
    requested_run = scope.get("requested_run") or scope.get("run")
    if requested_run:
        require(
            Path(requested_run.replace("/", "\\")).name
            == manifest_path.parent.name,
            "Review manifest does not belong to its requested run",
        )

    mode = scope["mode"]
    model = scope["model"]
    section = scope["section_id"]
    jobs = [
        job
        for job in manifest.get("jobs", [])
        if job.get("model") == model
        and job.get("section") == section
        and job.get("name") == mode
    ]
    require(len(jobs) == 1, "Expected exactly one matching run05 manifest job")
    job = jobs[0]
    require(
        job.get("status") == "completed" and job.get("exit_code") == 0,
        "Target manifest job is not completed successfully",
    )
    require(bool(job.get("graph_sha256")), "Target job has no graph SHA-256")
    graph_path = (Path(job["directory"]) / "graph.json").resolve(strict=True)
    actual_graph_sha = sha256_file(graph_path)
    require(
        actual_graph_sha == job["graph_sha256"],
        "Target job graph digest differs from the completed manifest row",
    )

    for claimed, actual, label in (
        (resolution.get("target_job_model"), model, "model"),
        (resolution.get("target_job_section"), section, "section"),
        (resolution.get("target_job_mode"), mode, "mode"),
    ):
        if claimed is not None:
            require(claimed == actual, f"Review target job {label} mismatch")
    for status_key in ("target_job_status", "job_status"):
        if status_key in resolution:
            require(
                resolution[status_key] == job["status"],
                "Review target job status differs from manifest",
            )
    for exit_key in ("target_job_exit_code", "exit_code"):
        if exit_key in resolution:
            require(
                resolution[exit_key] == job["exit_code"],
                "Review target job exit code differs from manifest",
            )
    if "matches_graph_binding" in resolution:
        require(resolution["matches_graph_binding"] is True,
                "Review says its graph binding does not match")
    for claimed_graph_sha in (
        resolution.get("manifest_graph_sha256"),
    ):
        if claimed_graph_sha is not None:
            require(
                claimed_graph_sha == job["graph_sha256"],
                "Review target graph digest differs from manifest",
            )
    for binding in review.get("sha256_bindings", []):
        if binding.get("manifest_graph_sha256") is not None:
            require(
                binding["manifest_graph_sha256"] == job["graph_sha256"],
                "Review graph binding differs from completed manifest job",
            )

    # The overall manifest hash is intentionally ignored: other experiment jobs
    # may update the manifest while a completed graph remains immutable.
    bindings = review.get("sha256_bindings", [])
    graph_bindings = []
    bound_paths: dict[Path, str] = {}
    for binding in bindings:
        binding_path = Path(binding["path"])
        if binding.get("role") == "run05_manifest" or binding_path.name.lower() == "manifest.json":
            continue
        resolved = resolve_path(binding_path)
        require(
            sha256_file(resolved) == binding["sha256"],
            f"SHA-256 mismatch for immutable review binding: {binding['path']}",
        )
        bound_paths[resolved] = binding["sha256"]
        if resolved == graph_path:
            graph_bindings.append(binding)

    require(len(graph_bindings) == 1, "Review must bind exactly the completed job graph")
    require(
        graph_bindings[0]["sha256"] == job["graph_sha256"],
        "Review graph binding differs from completed manifest job",
    )
    for optional_graph_path in (scope.get("graph_path"), resolution.get("graph_path")):
        if optional_graph_path:
            require(
                resolve_path(optional_graph_path) == graph_path,
                "Review graph path differs from completed manifest job",
            )

    reference_path = resolve_path(scope["reference_path"])
    source_path = resolve_path(scope["source_path"])
    require(reference_path in bound_paths, "Frozen reference has no SHA-256 binding")
    require(source_path in bound_paths, "Actual source has no SHA-256 binding")
    reference_sha = bound_paths[reference_path]
    source_sha = bound_paths[source_path]
    graph = read_json(graph_path)
    reference = read_json(reference_path)
    reference_sections = [
        row for row in reference.get("sections", []) if row.get("id") == scope["section_id"]
    ]
    require(len(reference_sections) == 1,
            "Frozen reference must contain exactly one reviewed section")
    reference_section = reference_sections[0]
    require(
        reference_section.get("source_sha256") == source_sha,
        "Bound source digest differs from frozen reference section digest",
    )
    if reference_section.get("source"):
        require(
            resolve_path(reference_section["source"]) == source_path,
            "Bound source path differs from frozen reference section source",
        )
    if scope.get("source_lines_inclusive") is not None:
        require(
            scope["source_lines_inclusive"]
            == [reference_section.get("start_line"), reference_section.get("end_line")],
            "Review source line bounds differ from frozen reference section",
        )
    return {
        "job": job,
        "manifest_path": manifest_path,
        "graph_path": graph_path,
        "graph_sha256": actual_graph_sha,
        "reference_path": reference_path,
        "reference_sha256": reference_sha,
        "source_path": source_path,
        "source_sha256": source_sha,
        "graph": graph,
        "reference": reference,
    }


def validate_review(review_path: Path) -> dict[str, Any]:
    review = read_json(review_path)
    scope = review["scope"]
    bound = _validate_bindings(review)
    graph = bound["graph"]
    reference = bound["reference"]
    section = scope["section_id"]

    graph_nodes = graph.get("nodes", [])
    graph_node_by_id = {node["id"]: node for node in graph_nodes}
    require(
        len(graph_node_by_id) == len(graph_nodes),
        "Graph contains duplicate node IDs",
    )
    graph_relations = graph.get("relations", [])
    graph_relation_by_id = {relation["id"]: relation for relation in graph_relations}
    require(
        len(graph_relation_by_id) == len(graph_relations),
        "Graph contains duplicate relation IDs",
    )
    for relation in graph_relations:
        require(
            relation.get("source_id") in graph_node_by_id
            and relation.get("target_id") in graph_node_by_id,
            "Graph relation references a missing node",
        )
    graph_triples = {_triplet(relation) for relation in graph_relations}

    mandatory = [
        node
        for node in reference.get("nodes", [])
        if node.get("section") == section and node.get("status") == "M"
    ]
    mandatory_by_id = {node["id"]: node for node in mandatory}
    require(len(mandatory_by_id) == len(mandatory), "Reference has duplicate mandatory IDs")
    annotations = review.get("mandatory_item_annotations", [])
    annotation_by_id = {row["item_id"]: row for row in annotations}
    require(
        len(annotation_by_id) == len(annotations)
        and set(annotation_by_id) == set(mandatory_by_id),
        "Review mandatory annotations do not exactly cover reference mandatory nodes",
    )

    strict_identity_by_item: dict[str, str] = {}
    used_identity_ids: set[str] = set()
    typed_identity_results = []
    for item_id, ref_node in mandatory_by_id.items():
        row = annotation_by_id[item_id]
        require(
            row.get("reference_type") == ref_node["type"]
            and row.get("reference_name") == ref_node["name"],
            f"Review reference identity metadata mismatch for {item_id}",
        )
        require(
            row.get("checklist") == ref_node.get("checklist", []),
            f"Review checklist does not match frozen reference for {item_id}",
        )
        content_ids = row.get("content_ids")
        require(isinstance(content_ids, list), f"Missing content_ids for {item_id}")
        require(len(set(content_ids)) == len(content_ids),
                f"Duplicate content carrier for {item_id}")
        for content_id in content_ids:
            require(
                content_id in graph_node_by_id,
                f"Unknown content carrier {content_id!r} for {item_id}",
            )
        identity_id = row.get("identity_id")
        status = row.get("identity_status")
        if identity_id is None:
            require(
                status != "strict_type_match",
                f"Null identity marked strict for {item_id}",
            )
            typed_identity_results.append(
                {
                    "item_id": item_id,
                    "reference_type": ref_node["type"],
                    "identity_id": None,
                    "identity_type": None,
                    "typed_identity_hit": False,
                }
            )
        else:
            require(identity_id in graph_node_by_id,
                    f"Unknown strict identity {identity_id!r} for {item_id}")
            require(identity_id in content_ids,
                    f"Strict identity is not listed as a content carrier for {item_id}")
            require(
                graph_node_by_id[identity_id].get("type") == ref_node["type"],
                f"Strict identity has the wrong type for {item_id}",
            )
            require(identity_id not in used_identity_ids,
                    "Mandatory identity assignment is not one-to-one")
            require(status == "strict_type_match",
                    f"Typed identity is not marked strict for {item_id}")
            used_identity_ids.add(identity_id)
            strict_identity_by_item[item_id] = identity_id
            typed_identity_results.append(
                {
                    "item_id": item_id,
                    "reference_type": ref_node["type"],
                    "identity_id": identity_id,
                    "identity_type": graph_node_by_id[identity_id]["type"],
                    "typed_identity_hit": True,
                }
            )
        require(
            isinstance(row.get("description_self_contained"), bool)
            and isinstance(row.get("source_checklist_complete"), bool),
            f"Reviewer judgment flags must be booleans for {item_id}",
        )

    predicted_nodes = review.get("predicted_node_admission", [])
    predicted_ids = [row["node_id"] for row in predicted_nodes]
    require(len(set(predicted_ids)) == len(predicted_ids),
            "Predicted node admission contains duplicate node IDs")
    for row in predicted_nodes:
        node_id = row["node_id"]
        require(node_id in graph_node_by_id,
                f"Predicted node {node_id!r} is missing from graph")
        actual = graph_node_by_id[node_id]
        require(
            row.get("type") == actual.get("type")
            and row.get("name") == actual.get("name"),
            f"Predicted node type/name mismatch for {node_id}",
        )
        for item_id in row.get("item_ids", []):
            if item_id in mandatory_by_id and item_id in strict_identity_by_item:
                require(
                    strict_identity_by_item[item_id] == node_id,
                    f"Admission row assigns the wrong strict identity for {item_id}",
                )

    reference_relations = [
        relation
        for relation in reference.get("relations_provisional", [])
        if relation.get("section") == section
    ]
    reference_relation_by_id = {relation["id"]: relation for relation in reference_relations}
    require(
        len(reference_relation_by_id) == len(reference_relations),
        "Reference has duplicate finite relation IDs",
    )
    review_relations = review.get("finite_reference_relations", [])
    review_relation_by_id = {row["reference_id"]: row for row in review_relations}
    require(
        len(review_relation_by_id) == len(review_relations)
        and set(review_relation_by_id) == set(reference_relation_by_id),
        "Review finite relations do not exactly cover frozen reference edges",
    )

    exact_relation_hits = 0
    relation_results = []
    for relation_id, ref_relation in reference_relation_by_id.items():
        row = review_relation_by_id[relation_id]
        source_item = ref_relation["start"]
        target_item = ref_relation["end"]
        require(
            source_item in mandatory_by_id and target_item in mandatory_by_id,
            f"Finite reference relation {relation_id} has a non-mandatory endpoint",
        )
        require(
            row.get("source_item") == source_item
            and row.get("target_item") == target_item
            and row.get("type") == ref_relation["type"],
            f"Review relation metadata mismatch for {relation_id}",
        )
        source_identity = strict_identity_by_item.get(source_item)
        target_identity = strict_identity_by_item.get(target_item)
        endpoints = row.get("strict_endpoint_ids", {})
        require(
            endpoints.get("source") == source_identity
            and endpoints.get("target") == target_identity,
            f"Review strict endpoints mismatch for {relation_id}",
        )
        hit = bool(
            source_identity
            and target_identity
            and (source_identity, ref_relation["type"], target_identity)
            in graph_triples
        )
        require(
            isinstance(row.get("exact_match"), bool)
            and row["exact_match"] == hit,
            f"Annotated finite relation hit differs from graph/reference for {relation_id}",
        )
        exact_relation_hits += int(hit)
        relation_results.append(
            {
                "reference_id": relation_id,
                "source_item": source_item,
                "type": ref_relation["type"],
                "target_item": target_item,
                "strict_source_id": source_identity,
                "strict_target_id": target_identity,
                "exact_graph_hit": hit,
            }
        )

        candidate = row.get("predicted_relation")
        if candidate is None:
            candidate = row.get("nearest_predicted_edge")
        if candidate is not None:
            candidate_relation = graph_relation_by_id.get(candidate.get("relation_id"))
            require(candidate_relation is not None,
                    f"Predicted candidate edge is absent for {relation_id}")
            require(
                _triplet(candidate) == _triplet(candidate_relation),
                f"Predicted candidate edge differs from graph for {relation_id}",
            )

    counts = {
        "mandatory_typed_identity_hits": len(strict_identity_by_item),
        "mandatory_items": len(mandatory),
        "combined_carrier_checklists_marked_complete": sum(
            row["source_checklist_complete"] for row in annotations
        ),
        "self_contained_descriptions_marked_true": sum(
            row["description_self_contained"] for row in annotations
        ),
        "required_finite_relations_exact_strict_hits": exact_relation_hits,
        "required_finite_relations": len(reference_relations),
        "predicted_nodes_audited": len(predicted_nodes),
        "accepted_optional_extras": sum(
            row.get("admission") == "accepted_optional_extra"
            for row in predicted_nodes
        ),
        "obvious_duplicate_pairs": len(
            review.get("duplicate_findings", {}).get("exact_or_obvious_duplicates", [])
        ),
    }
    source_support = review.get("source_support_audit")
    if source_support is not None:
        unsupported_nodes = len(source_support.get("unsupported_node_ids", []))
        unsupported_relations = len(source_support.get("unsupported_relation_ids", []))
        counts["reviewer_reported_unsupported_nodes"] = unsupported_nodes
        counts["reviewer_reported_unsupported_relations"] = unsupported_relations
        counts["reviewer_reported_unsupported_claims"] = (
            unsupported_nodes + unsupported_relations
        )

    declared_to_recomputed = {
        "mandatory_typed_identity_hits": "mandatory_typed_identity_hits",
        "mandatory_items": "mandatory_items",
        "combined_carrier_checklists_complete": "combined_carrier_checklists_marked_complete",
        "self_contained_descriptions": "self_contained_descriptions_marked_true",
        "required_finite_relations_exact_strict_hits": "required_finite_relations_exact_strict_hits",
        "required_finite_relations": "required_finite_relations",
        "predicted_nodes_audited": "predicted_nodes_audited",
        "accepted_optional_extras": "accepted_optional_extras",
        "obvious_duplicate_pairs": "obvious_duplicate_pairs",
        "unsupported_predicted_claims": "reviewer_reported_unsupported_claims",
    }
    declared = review.get("summary_counts", {})
    discrepancies = [
        {
            "field": declared_key,
            "declared": declared[declared_key],
            "recomputed": counts[recomputed_key],
        }
        for declared_key, recomputed_key in declared_to_recomputed.items()
        if declared_key in declared and recomputed_key in counts
        and declared[declared_key] != counts[recomputed_key]
    ]

    return {
        "review": review_path.parent.name,
        "reviewer": review.get("reviewer"),
        "model": scope["model"],
        "section_id": section,
        "mode": scope["mode"],
        "graph_path": str(bound["graph_path"]),
        "completed_job_directory": str(Path(bound["job"]["directory"]).resolve()),
        "graph_sha256": bound["graph_sha256"],
        "reference_path": str(bound["reference_path"]),
        "reference_sha256": bound["reference_sha256"],
        "source_path": str(bound["source_path"]),
        "source_sha256": bound["source_sha256"],
        "typed_identity_results": typed_identity_results,
        "finite_relation_results": relation_results,
        "recomputed_counts": counts,
        "review_summary_count_discrepancies": discrepancies,
    }


def build_summary(review_dirs: tuple[tuple[str, Path], ...] = REVIEW_DIRS) -> dict[str, Any]:
    rows = []
    for label, directory in review_dirs:
        result = validate_review(directory / "review.json")
        result["review"] = label
        rows.append(result)
    return {
        "schema_version": "supplementary-taylor-validated-summary.v1",
        "status": "mechanical artifact, graph, reference, identity, and finite-edge checks passed",
        "review_independence": (
            "These are assistant source reviews, not independent human annotations or human Gold."
        ),
        "annotation_limits": (
            "Self-contained and combined-carrier checklist totals are recomputed counts of reviewer booleans only; "
            "their semantic judgments are not mechanically verified. Unsupported-claim and duplicate totals, "
            "when present, are likewise reviewer-reported judgments."
        ),
        "manifest_policy": (
            "The mutable overall manifest SHA-256 is ignored. Each reviewed graph must match the unique "
            "completed, zero-exit job row and the graph's immutable SHA-256 binding."
        ),
        "rows": rows,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Supplementary Taylor Review Validation",
        "",
        summary["status"] + ".",
        "",
        summary["review_independence"],
        "",
        summary["annotation_limits"],
        "",
        "The overall manifest digest is not checked because the manifest is mutable; the target completed-job graph digest is checked.",
        "",
        "| Review | Model | Mode | Typed identities | Exact finite edges | Checklist marked complete | Self-contained marked true | Graph SHA-256 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in summary["rows"]:
        counts = row["recomputed_counts"]
        lines.append(
            "| {review} | {model} | {mode} | {ids}/{items} | {edges}/{total} | {checklists}/{items} | {self_contained}/{items} | `{sha}` |".format(
                review=row["review"],
                model=row["model"],
                mode=row["mode"],
                ids=counts["mandatory_typed_identity_hits"],
                items=counts["mandatory_items"],
                edges=counts["required_finite_relations_exact_strict_hits"],
                total=counts["required_finite_relations"],
                checklists=counts["combined_carrier_checklists_marked_complete"],
                self_contained=counts["self_contained_descriptions_marked_true"],
                sha=row["graph_sha256"],
            )
        )
    for row in summary["rows"]:
        lines.extend(["", f"## {row['review']}", "", "Typed identities:"])
        for identity in row["typed_identity_results"]:
            outcome = identity["identity_id"] or "missing"
            lines.append(f"- {identity['item_id']}: {outcome}")
        lines.extend(["", "Finite reference edges:"])
        for relation in row["finite_relation_results"]:
            outcome = "hit" if relation["exact_graph_hit"] else "miss"
            source_id = relation["strict_source_id"] or "missing"
            target_id = relation["strict_target_id"] or "missing"
            lines.append(
                f"- {relation['reference_id']}: {source_id} -[{relation['type']}]-> "
                f"{target_id}: {outcome}"
            )
    lines.extend(["", "Counts are recalculated from the frozen reference, graph, and review annotations.", ""])
    return "\n".join(lines)


def write_summary(output_dir: Path = REVIEW_ROOT) -> dict[str, Any]:
    summary = build_summary()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "supplementary_validated_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output_dir / "supplementary_validated_summary.md").write_text(
        render_markdown(summary), encoding="utf-8", newline="\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REVIEW_ROOT,
        help="Directory for supplementary_validated_summary.json and .md",
    )
    args = parser.parse_args()
    print(json.dumps(write_summary(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
