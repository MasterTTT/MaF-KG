import hashlib
import json
from pathlib import Path

import pytest

from scripts import validate_supplementary_taylor_reviews as validator


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixture_project(root: Path, schema: str = "kimi") -> tuple[Path, Path]:
    source = root / "source.md"
    source.write_text("Taylor source\n", encoding="utf-8")
    run_root = root / "evaluation" / "prospective_2026-10-04" / "multimodel_run05"
    reference_path = run_root / "snapshot" / "reference.json"
    reference = {
        "sections": [
            {
                "id": "ch3_s3", "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "start_line": 1, "end_line": 1,
            }
        ],
        "nodes": [
            {
                "id": "T-N1", "section": "ch3_s3", "status": "M",
                "type": "Concept", "name": "Taylor polynomial", "checklist": ["basis"],
            },
            {
                "id": "T-N2", "section": "ch3_s3", "status": "M",
                "type": "Claim", "name": "Peano Taylor formula", "checklist": ["remainder"],
            },
        ],
        "relations_provisional": [
            {
                "id": "R01", "section": "ch3_s3", "start": "T-N2",
                "type": "involves", "end": "T-N1", "evidence": [1, 2],
            }
        ],
    }
    _write_json(reference_path, reference)

    model = {"luna": "gpt-6-luna", "mimo": "mimo-v2.6-flash", "kimi": "kimi-k2.6"}[schema]
    mode = "direct" if schema == "kimi" else "sequential"
    graph_dir = (
        root / "evaluation" / "prospective_2026-10-04" / "multimodel_run02"
        if schema == "luna"
        else run_root
    ) / model / "ch3_s3" / mode
    graph_path = graph_dir / "graph.json"
    graph = {
        "nodes": [
            {"id": "concept_a", "type": "Concept", "name": "Taylor polynomial"},
            {"id": "claim_b", "type": "Claim", "name": "Peano Taylor formula"},
        ],
        "relations": [
            {
                "id": "edge_1", "type": "involves", "source_id": "claim_b",
                "target_id": "concept_a",
            }
        ],
    }
    _write_json(graph_path, graph)
    graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    manifest_path = run_root / "manifest.json"
    _write_json(
        manifest_path,
        {
            "status": "running",
            "jobs": [
                {
                    "model": model, "section": "ch3_s3", "name": mode,
                    "directory": str(graph_dir), "status": "completed",
                    "exit_code": 0, "graph_sha256": graph_sha,
                }
            ],
        },
    )

    scope = {
        "model": model, "section_id": "ch3_s3", "mode": mode,
        "source_path": str(source), "reference_path": str(reference_path),
        "source_lines_inclusive": [1, 1],
        "manifest_resolution": {
            "manifest_path": str(manifest_path),
            "manifest_graph_sha256": graph_sha,
        },
    }
    if schema == "luna":
        scope.update({"requested_run": "evaluation/prospective_2026-10-04/multimodel_run05"})
        scope["manifest_resolution"].update({
            "target_job_status": "completed", "target_job_exit_code": 0,
            "manifest_directory": str(graph_dir), "graph_path": str(graph_path),
        })
    elif schema == "mimo":
        scope.update({"requested_run": "multimodel_run05", "graph_path": str(graph_path)})
        scope["manifest_resolution"].update({"job_status": "completed", "exit_code": 0})
    else:
        scope.update({"run": "evaluation/prospective_2026-10-04/multimodel_run05"})
        scope["manifest_resolution"].update({
            "target_job_model": model, "target_job_section": "ch3_s3",
            "target_job_mode": mode, "target_job_status": "completed",
            "target_job_exit_code": 0, "graph_path": str(graph_path),
        })

    bindings = [
        {"role": "source", "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        {"role": "frozen_reference", "path": str(reference_path), "sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest()},
        {"role": "completed_graph", "path": str(graph_path), "sha256": graph_sha},
    ]
    if schema == "luna":
        bindings.insert(0, {"role": "run05_manifest", "path": str(manifest_path), "sha256": "0" * 64})

    annotations = [
        {
            "item_id": "T-N1", "reference_type": "Concept", "reference_name": "Taylor polynomial",
            "checklist": ["basis"], "identity_id": "concept_a", "identity_status": "strict_type_match",
            "content_ids": ["concept_a"], "description_self_contained": True,
            "source_checklist_complete": True,
        },
        {
            "item_id": "T-N2", "reference_type": "Claim", "reference_name": "Peano Taylor formula",
            "checklist": ["remainder"], "identity_id": "claim_b", "identity_status": "strict_type_match",
            "content_ids": ["claim_b"], "description_self_contained": False,
            "source_checklist_complete": True,
        },
    ]
    predicted_edge = {
        "relation_id": "edge_1", "type": "involves", "source_id": "claim_b",
        "target_id": "concept_a",
    }
    relation_row = {
        "reference_id": "R01", "source_item": "T-N2", "type": "involves",
        "target_item": "T-N1", "strict_endpoint_ids": {"source": "claim_b", "target": "concept_a"},
        "exact_match": True, "reason": "fixture",
    }
    relation_row["predicted_relation" if schema == "kimi" else "nearest_predicted_edge"] = predicted_edge
    review = {
        "reviewer": "assistant fixture", "scope": scope, "sha256_bindings": bindings,
        "mandatory_item_annotations": annotations,
        "finite_reference_relations": [relation_row],
        "predicted_node_admission": [
            {"node_id": node["id"], "type": node["type"], "name": node["name"], "admission": "required_identity"}
            for node in graph["nodes"]
        ],
        "duplicate_findings": {"exact_or_obvious_duplicates": []},
        "summary_counts": {"mandatory_typed_identity_hits": 999},
    }
    review_path = root / "preliminary_taylor_review" / schema / "review.json"
    _write_json(review_path, review)
    return review_path, graph_path


@pytest.mark.parametrize("schema", ["luna", "mimo", "kimi"])
def test_validates_distinct_review_bindings_and_edge_schemas(tmp_path, schema):
    review_path, _ = _fixture_project(tmp_path, schema)

    result = validator.validate_review(review_path)

    assert result["recomputed_counts"]["mandatory_typed_identity_hits"] == 2
    assert result["recomputed_counts"]["required_finite_relations_exact_strict_hits"] == 1
    assert len(result["typed_identity_results"]) == 2
    assert result["finite_relation_results"][0]["exact_graph_hit"] is True
    assert result["review_summary_count_discrepancies"] == [
        {"field": "mandatory_typed_identity_hits", "declared": 999, "recomputed": 2}
    ]


def test_rejects_review_claim_of_relation_hit_not_in_graph(tmp_path):
    review_path, graph_path = _fixture_project(tmp_path)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["relations"] = []
    _write_json(graph_path, graph)
    manifest_path = tmp_path / "evaluation" / "prospective_2026-10-04" / "multimodel_run05" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["jobs"][0]["graph_sha256"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    graph_binding = next(row for row in review["sha256_bindings"] if row["role"] == "completed_graph")
    graph_binding["sha256"] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    review["scope"]["manifest_resolution"]["manifest_graph_sha256"] = graph_binding["sha256"]
    _write_json(review_path, review)

    with pytest.raises(ValueError, match="Annotated finite relation hit"):
        validator.validate_review(review_path)


def test_rejects_immutable_hash_mismatch(tmp_path):
    review_path, _ = _fixture_project(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("mutated source\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validator.validate_review(review_path)
