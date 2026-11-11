"""Offline closeout scoring for the WWW prerequisite supplement E2 runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROLE_LABELS = ("required", "proof_support", "principle_support", "related_or_none")
UNCERTAIN_ROLE = "uncertain"
EXPECTED_RUNS = (("P", 1), ("P", 2), ("P_plus_S", 1), ("P_plus_S", 2))
E2_RELATIVE = Path("evaluation/www_evidence_supplement_2026-10-11/e2_prerequisite")
REFERENCE_V3_RELATIVE = Path(
    "evaluation/paper_closeout_2026-10-10/final_closeout_v1/"
    "prerequisite_adjudication/reference_v3.json"
)
OUTPUT_RELATIVE = Path(
    "evaluation/www_evidence_supplement_2026-10-11/closeout_v2/prerequisite"
)
JSON_NAME = "www_supplement_prerequisite_closeout.json"
MARKDOWN_NAME = "www_supplement_prerequisite_closeout.md"

Edge = tuple[str, str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _model_edge(row: Mapping[str, Any]) -> Edge:
    """Model source is the learning goal; model target is its prerequisite."""
    return str(row["source_ku"]), str(row["target_ku"])


def _reference_edge(row: Mapping[str, Any]) -> Edge:
    """Reference target_ku is the learning goal, not the prerequisite."""
    return str(row["target_ku"]), str(row["prerequisite_ku"])


def _edge_record(edge: Edge) -> dict[str, str]:
    source, target = edge
    return {
        "source_ku": source,
        "target_ku": target,
        "statement": f"{source} depends_on {target}",
    }


def _assert_unique_edges(edges: Iterable[Edge], description: str) -> set[Edge]:
    rows = list(edges)
    result = set(rows)
    if len(rows) != len(result):
        raise ValueError(f"duplicate edge in {description}")
    return result


def validate_reference_pair_consistency(
    reference_v3: Mapping[str, Any], reference_v4: Mapping[str, Any]
) -> dict[str, Any]:
    """Check carried-forward v3 labels and direction before scoring either reference."""
    old_pairs = list(reference_v3.get("pairs", []))
    new_pairs = list(reference_v4.get("pairs", []))
    old_by_id = {str(pair["id"]): pair for pair in old_pairs}
    new_by_id = {str(pair["id"]): pair for pair in new_pairs}
    if len(old_by_id) != len(old_pairs) or len(new_by_id) != len(new_pairs):
        raise ValueError("reference pair IDs must be unique")
    _assert_unique_edges((_reference_edge(pair) for pair in old_pairs), "reference v3")
    _assert_unique_edges((_reference_edge(pair) for pair in new_pairs), "reference v4")

    mismatches: list[dict[str, Any]] = []
    for pair_id, old in old_by_id.items():
        current = new_by_id.get(pair_id)
        if current is None:
            mismatches.append({"pair_id": pair_id, "problem": "missing_from_v4"})
            continue
        old_identity = (_reference_edge(old), old.get("role"), old.get("directness"))
        new_identity = (_reference_edge(current), current.get("role"), current.get("directness"))
        if old_identity != new_identity:
            mismatches.append({"pair_id": pair_id, "problem": "endpoint_or_label_changed"})
    if mismatches:
        raise ValueError(f"reference v3 labels differ from v4: {mismatches}")

    return {
        "consistent": True,
        "v3_pairs_checked": len(old_pairs),
        "v4_pairs": len(new_pairs),
        "carried_forward_pair_ids": sorted(old_by_id),
        "direction_check": "target_ku (learning goal) depends_on prerequisite_ku",
    }


def binary_metrics(
    predicted: set[Edge], gold: set[Edge], decisive_reference_edges: set[Edge]
) -> dict[str, Any]:
    """Score only predictions with a decisive reference verdict; preserve uncovered edges."""
    tp_edges = predicted & gold
    adjudicated_predictions = predicted & decisive_reference_edges
    fp_edges = adjudicated_predictions - gold
    fn_edges = gold - predicted
    unadjudicated = predicted - decisive_reference_edges
    tp, fp, fn = len(tp_edges), len(fp_edges), len(fn_edges)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    if precision is None or recall is None:
        f1 = None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "predicted_positive_count": len(predicted),
        "adjudicated_prediction_count": len(adjudicated_predictions),
        "unadjudicated_prediction_count": len(unadjudicated),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp_edges": sorted(tp_edges),
        "fp_edges": sorted(fp_edges),
        "fn_edges": sorted(fn_edges),
        "unadjudicated_edges": sorted(unadjudicated),
    }


def _round_metric(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _public_binary_metrics(metrics: Mapping[str, Any], reference_by_edge: Mapping[Edge, Mapping[str, Any]]) -> dict[str, Any]:
    def describe(edges: Sequence[Edge]) -> list[dict[str, Any]]:
        described = []
        for edge in edges:
            item = _edge_record(edge)
            reference = reference_by_edge.get(edge)
            if reference:
                item["reference_pair_id"] = str(reference["id"])
                item["reference_role"] = str(reference.get("role"))
                item["reference_directness"] = reference.get("directness")
            described.append(item)
        return described

    return {
        "counts": {
            key: metrics[key]
            for key in (
                "tp",
                "fp",
                "fn",
                "predicted_positive_count",
                "adjudicated_prediction_count",
                "unadjudicated_prediction_count",
            )
        },
        "precision": _round_metric(metrics["precision"]),
        "recall": _round_metric(metrics["recall"]),
        "f1": _round_metric(metrics["f1"]),
        "tp_edges": describe(metrics["tp_edges"]),
        "fp_edges": describe(metrics["fp_edges"]),
        "fn_edges": describe(metrics["fn_edges"]),
        "unadjudicated_edges": describe(metrics["unadjudicated_edges"]),
    }


def _candidate_recall(candidate_edges: set[Edge], gold_required: set[Edge]) -> dict[str, Any]:
    hits = candidate_edges & gold_required
    return {
        "candidate_edge_count": len(candidate_edges),
        "required_edges_recalled": len(hits),
        "required_edge_total": len(gold_required),
        "recall": _round_metric(len(hits) / len(gold_required)) if gold_required else None,
        "recalled_edges": [_edge_record(edge) for edge in sorted(hits)],
        "missed_required_edges": [_edge_record(edge) for edge in sorted(gold_required - hits)],
    }


def _score_reference(run: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    target_ids = {str(target["unit_id"]) for target in reference.get("targets", [])}
    all_pairs = [pair for pair in reference.get("pairs", []) if str(pair["target_ku"]) in target_ids]
    by_edge = {_reference_edge(pair): pair for pair in all_pairs}
    if len(by_edge) != len(all_pairs):
        raise ValueError("reference has duplicate endpoint pairs")
    decisive_by_edge = {edge: pair for edge, pair in by_edge.items() if pair.get("role") != UNCERTAIN_ROLE}
    decisive_edges = set(decisive_by_edge)
    unresolved_edges = set(by_edge) - decisive_edges
    required_pairs = {
        edge: pair for edge, pair in decisive_by_edge.items() if pair.get("role") == "required"
    }
    gold_required = set(required_pairs)
    gold_direct = {
        edge for edge, pair in required_pairs.items() if pair.get("directness") == "direct"
    }
    gold_indirect = {
        edge for edge, pair in required_pairs.items() if pair.get("directness") == "indirect"
    }
    if gold_direct | gold_indirect != gold_required:
        raise ValueError("every required reference pair must be classified direct or indirect")

    predictions = {
        _model_edge(row): str(row["label"])
        for row in run["predictions"]
        if _model_edge(row)[0] in target_ids
    }
    if len(predictions) != sum(_model_edge(row)[0] in target_ids for row in run["predictions"]):
        raise ValueError("duplicate actual prediction edge")
    unknown_labels = sorted(set(predictions.values()) - set(ROLE_LABELS) - {UNCERTAIN_ROLE})
    if unknown_labels:
        raise ValueError(f"unknown prediction roles: {unknown_labels}")

    predicted_required = {edge for edge, label in predictions.items() if label == "required"}
    direct_edges = {edge for edge in run["direct_edges"] if edge[0] in target_ids}
    indirect_edges = {edge for edge in run["indirect_edges"] if edge[0] in target_ids}
    required_metrics = binary_metrics(predicted_required, gold_required, decisive_edges)
    direct_metrics = binary_metrics(direct_edges, gold_direct, decisive_edges)

    role_matrix = {gold_role: {pred_role: 0 for pred_role in ROLE_LABELS} for gold_role in ROLE_LABELS}
    covered_edges = decisive_edges & {edge for edge, role in predictions.items() if role != UNCERTAIN_ROLE}
    for edge in covered_edges:
        gold_role = str(decisive_by_edge[edge]["role"])
        role_matrix[gold_role][predictions[edge]] += 1

    support_upgrades = []
    for edge in sorted(predicted_required & decisive_edges):
        pair = decisive_by_edge[edge]
        if pair.get("role") in {"proof_support", "principle_support"}:
            item = _edge_record(edge)
            item.update(
                {
                    "reference_pair_id": str(pair["id"]),
                    "reference_role": str(pair["role"]),
                    "predicted_role": predictions[edge],
                    "predicted_directness": "direct" if edge in direct_edges else "indirect",
                }
            )
            support_upgrades.append(item)

    indirect_upgrades = []
    for edge in sorted(direct_edges & gold_indirect):
        pair = decisive_by_edge[edge]
        item = _edge_record(edge)
        item.update(
            {
                "reference_pair_id": str(pair["id"]),
                "reference_role": "required",
                "reference_directness": "indirect",
                "predicted_directness": "direct",
            }
        )
        indirect_upgrades.append(item)

    candidate_scope = {
        name: {edge for edge in run["candidate_sets"][name] if edge[0] in target_ids}
        for name in ("projection", "semantic_additions", "merged")
    }
    candidate_recall = {
        name: _candidate_recall(edges, gold_required)
        for name, edges in candidate_scope.items()
    }
    direct_candidate_recall = {
        name: _candidate_recall(edges, gold_direct)
        for name, edges in candidate_scope.items()
    }

    missing_required = []
    for edge in sorted(gold_required - predicted_required):
        pair = required_pairs[edge]
        if edge not in candidate_scope["merged"]:
            outcome = "not_generated_in_merged_candidates"
        elif edge not in predictions:
            outcome = "candidate_without_actual_verdict"
        else:
            outcome = f"actual_verdict_{predictions[edge]}"
        item = _edge_record(edge)
        item.update({"reference_pair_id": str(pair["id"]), "miss_reason": outcome})
        missing_required.append(item)

    outside_reference = []
    unresolved_acceptances = []
    for edge in sorted(run["predicted_required_all"]):
        if edge in by_edge:
            if edge in unresolved_edges:
                item = _edge_record(edge)
                item.update({"reference_pair_id": str(by_edge[edge]["id"]), "reason": "reference_uncertain"})
                unresolved_acceptances.append(item)
            continue
        if edge[0] not in target_ids:
            reason = "learning_goal_outside_reference_target_scope"
        else:
            reason = "pair_not_enumerated_in_reference"
        item = _edge_record(edge)
        item.update(
            {
                "reason": reason,
                "predicted_directness": "direct" if edge in run["direct_edges"] else "indirect",
            }
        )
        outside_reference.append(item)

    covered_count = len(covered_edges)
    decisive_count = len(decisive_edges)
    return {
        "reference_name": str(reference.get("version", reference.get("experiment", "reference"))),
        "target_count": len(target_ids),
        "reference_pair_count": len(all_pairs),
        "decisive_reference_pair_count": decisive_count,
        "uncertain_pair_count_excluded": len(unresolved_edges),
        "actual_verdict_coverage": {
            "covered_decisive_pairs": covered_count,
            "decisive_reference_pairs": decisive_count,
            "ratio": _round_metric(covered_count / decisive_count) if decisive_count else None,
            "missing_decisive_pair_ids": sorted(
                str(by_edge[edge]["id"]) for edge in decisive_edges - set(predictions)
            ),
            "definition": "intersection of actual predictions and reference pairs whose role is not uncertain",
        },
        "direct_required": _public_binary_metrics(direct_metrics, by_edge),
        "all_required": _public_binary_metrics(required_metrics, by_edge),
        "role_confusion": {
            "rows_reference_role_columns_predicted_role": role_matrix,
            "evaluated_pair_count": len(covered_edges),
            "support_upgrade_error_count": len(support_upgrades),
            "support_upgrade_errors": support_upgrades,
            "indirect_to_direct_upgrade_error_count": len(indirect_upgrades),
            "indirect_to_direct_upgrade_errors": indirect_upgrades,
        },
        "missing_required_edges": missing_required,
        "candidate_recall": candidate_recall,
        "direct_candidate_recall": direct_candidate_recall,
        "accepted_required_edges_outside_reference": {
            "count": len(outside_reference),
            "direct_count": sum(edge in run["direct_edges"] for edge in run["predicted_required_all"] if edge not in by_edge),
            "indirect_count": sum(edge in run["indirect_edges"] for edge in run["predicted_required_all"] if edge not in by_edge),
            "edges": outside_reference,
            "interpretation": "listed as unadjudicated, not counted as false positives",
        },
        "accepted_required_edges_on_uncertain_reference_pairs": unresolved_acceptances,
        "scoring_note": (
            "精确率仅统计落在已枚举且非 uncertain 参考 pair 上的预测；未列边与 uncertain pair 排除，未列接受边另行报告。"
        ),
    }


def _macro_summary(rounds: Sequence[Mapping[str, Any]], path: Sequence[str]) -> dict[str, Any]:
    def value_at(row: Mapping[str, Any]) -> float | None:
        current: Any = row
        for key in path:
            current = current[key]
        return current

    values = [value_at(row) for row in rounds]
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return {"n": 0, "mean": None, "min": None, "max": None, "range": None}
    low, high = min(numeric), max(numeric)
    return {
        "n": len(numeric),
        "mean": _round_metric(sum(numeric) / len(numeric)),
        "min": _round_metric(low),
        "max": _round_metric(high),
        "range": _round_metric(high - low),
    }


def _aggregate_reference(rounds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metric_paths = {
        "direct_precision": ("direct_required", "precision"),
        "direct_recall": ("direct_required", "recall"),
        "direct_f1": ("direct_required", "f1"),
        "all_required_precision": ("all_required", "precision"),
        "all_required_recall": ("all_required", "recall"),
        "all_required_f1": ("all_required", "f1"),
        "actual_verdict_coverage": ("actual_verdict_coverage", "ratio"),
        "projection_candidate_recall": ("candidate_recall", "projection", "recall"),
        "semantic_addition_candidate_recall": ("candidate_recall", "semantic_additions", "recall"),
        "merged_candidate_recall": ("candidate_recall", "merged", "recall"),
        "projection_direct_candidate_recall": ("direct_candidate_recall", "projection", "recall"),
        "semantic_addition_direct_candidate_recall": ("direct_candidate_recall", "semantic_additions", "recall"),
        "merged_direct_candidate_recall": ("direct_candidate_recall", "merged", "recall"),
    }
    result: dict[str, Any] = {
        "run_count": len(rounds),
        "macro_mean_and_range_across_runs": {
            name: _macro_summary(rounds, path) for name, path in metric_paths.items()
        },
        "by_arm": {},
    }
    arms = sorted({str(row["arm"]) for row in rounds})
    for arm in arms:
        selected = [row for row in rounds if row["arm"] == arm]
        result["by_arm"][arm] = {
            "repeat_count": len(selected),
            "mean_and_range": {
                name: _macro_summary(selected, path) for name, path in metric_paths.items()
            },
        }
    return result


def _load_run(repo_root: Path, arm: str, repeat: int) -> dict[str, Any]:
    run_dir = repo_root / E2_RELATIVE / "run_v3" / "runs" / arm / f"r{repeat}"
    manifest_path = run_dir / "run_manifest.json"
    predictions_path = run_dir / "predictions.json"
    candidates_path = run_dir / "candidate_log.json"
    merged_path = run_dir / "merged_candidates.json"
    accepted_path = run_dir / "accepted_direct_edges.json"
    manifest = _read_json(manifest_path)
    predictions_file = _read_json(predictions_path)
    candidate_log = _read_json(candidates_path)
    merged_file = _read_json(merged_path)
    accepted_file = _read_json(accepted_path)

    if manifest.get("status") != "completed":
        raise ValueError(f"run is not completed: {arm}/r{repeat}")
    manifest_arm = str(manifest.get("arm", "")).replace("+", "_plus_")
    if manifest_arm != arm or int(manifest.get("repeat", -1)) != repeat:
        raise ValueError(f"run manifest identity mismatch: {arm}/r{repeat}")

    review_paths = sorted(run_dir.glob("review_batch_*.json"))
    if not review_paths:
        raise ValueError(f"no review batches in {run_dir}")
    manifest_reviews = {str(Path(item["path"]).as_posix()): item for item in manifest.get("review_batches", [])}
    review_bindings = []
    for review_path in review_paths:
        relative = review_path.relative_to(repo_root).as_posix()
        digest = sha256_file(review_path)
        record = manifest_reviews.get(relative)
        if record is None or str(record.get("sha256", "")).lower() != digest:
            raise ValueError(f"review batch is not bound by run manifest: {relative}")
        review_bindings.append({"path": relative, "sha256": digest})
    if len(review_bindings) != len(manifest_reviews):
        raise ValueError(f"manifest review batch set differs from files: {arm}/r{repeat}")

    projection_binding = manifest.get("projection", {})
    source_path = (repo_root / str(projection_binding["path"])).resolve()
    source_digest = sha256_file(source_path)
    if source_digest.lower() != str(projection_binding.get("sha256", "")).lower():
        raise ValueError(f"projection source hash does not match manifest: {source_path}")

    review_digest = _canonical_sha256(review_bindings)
    direct_edges = _assert_unique_edges(
        (_model_edge(row) for row in accepted_file.get("edges", [])), "accepted_direct_edges"
    )
    classes = accepted_file.get("classes", {})
    required_direct = _assert_unique_edges(
        (_model_edge(row) for row in classes.get("required_direct", [])), "required_direct class"
    )
    indirect_edges = _assert_unique_edges(
        (_model_edge(row) for row in classes.get("required_indirect", [])), "required_indirect class"
    )
    if direct_edges != required_direct:
        raise ValueError(f"accepted direct edges disagree with required_direct class: {arm}/r{repeat}")

    prediction_rows = list(predictions_file.get("verdicts", []))
    prediction_edges = [_model_edge(row) for row in prediction_rows]
    _assert_unique_edges(prediction_edges, "predictions")
    predicted_required = {
        _model_edge(row) for row in prediction_rows if row.get("label") == "required"
    }
    if direct_edges & indirect_edges or direct_edges | indirect_edges != predicted_required:
        raise ValueError(f"direct/indirect classes do not partition required predictions: {arm}/r{repeat}")

    projection_rows = candidate_log.get("projection_log", [])
    semantic_rows = candidate_log.get("semantic_log", [])
    merged_rows = merged_file.get("candidates", [])
    projection_edges = _assert_unique_edges(
        (
            _model_edge(row)
            for row in projection_rows
            if row.get("legal") is True and row.get("duplicate_status") == "unique"
        ),
        "legal projection candidates",
    )
    semantic_candidates = _assert_unique_edges(
        (
            _model_edge(row)
            for row in semantic_rows
            if row.get("legal") is True
        ),
        "legal semantic candidates",
    )
    semantic_additions = semantic_candidates - projection_edges
    merged_edges = _assert_unique_edges((_model_edge(row) for row in merged_rows), "merged candidates")
    merge_log_unique = {
        _model_edge(row)
        for row in candidate_log.get("merge_log", [])
        if row.get("legal") is True and row.get("duplicate_status") == "unique"
    }
    if merged_edges != merge_log_unique:
        raise ValueError(f"merged candidate file disagrees with merge log: {arm}/r{repeat}")
    if projection_edges | semantic_additions != merged_edges:
        raise ValueError(f"projection plus semantic additions do not reproduce merged candidates: {arm}/r{repeat}")
    if not predicted_required <= merged_edges or not set(prediction_edges) <= merged_edges:
        raise ValueError(f"actual predictions are not bounded by merged candidates: {arm}/r{repeat}")

    review_hasher = hashlib.sha256()
    for item in review_bindings:
        review_hasher.update(item["path"].encode("utf-8"))
        review_hasher.update(b"\0")
        review_hasher.update(item["sha256"].encode("ascii"))
        review_hasher.update(b"\n")

    target_selection_path = repo_root / E2_RELATIVE / "target_selection.json"
    return {
        "arm": arm,
        "manifest_arm": manifest.get("arm"),
        "repeat": repeat,
        "status": manifest["status"],
        "model": manifest.get("model"),
        "provider": manifest.get("provider"),
        "manifest": manifest,
        "predictions": prediction_rows,
        "predicted_required_all": predicted_required,
        "direct_edges": direct_edges,
        "indirect_edges": indirect_edges,
        "candidate_sets": {
            "projection": projection_edges,
            "semantic_additions": semantic_additions,
            "merged": merged_edges,
        },
        "provenance": {
            "source": {
                "path": source_path.relative_to(repo_root.resolve()).as_posix(),
                "sha256": source_digest,
                "manifest_sha256_match": True,
            },
            "target_selection": {
                "path": target_selection_path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(target_selection_path),
            },
            "actual_prediction": {
                "path": predictions_path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(predictions_path),
            },
            "review": {
                "files": review_bindings,
                "aggregate_sha256": review_hasher.hexdigest(),
                "canonical_binding_sha256": review_digest,
                "actual_verdict_artifact": {
                    "path": predictions_path.relative_to(repo_root).as_posix(),
                    "sha256": sha256_file(predictions_path),
                },
            },
            "config": {
                "path": manifest_path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(manifest_path),
                "target_selection_sha256": sha256_file(target_selection_path),
            },
            "candidate_log": {
                "path": candidates_path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(candidates_path),
            },
            "merged_candidates": {
                "path": merged_path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(merged_path),
            },
            "accepted_direct_edges": {
                "path": accepted_path.relative_to(repo_root).as_posix(),
                "sha256": sha256_file(accepted_path),
            },
        },
        "run_counts": {
            "prediction_count": len(prediction_rows),
            "required_prediction_count": len(predicted_required),
            "direct_accepted_count": len(direct_edges),
            "indirect_required_count": len(indirect_edges),
            "projection_candidate_count": len(projection_edges),
            "semantic_addition_count": len(semantic_additions),
            "merged_candidate_count": len(merged_edges),
        },
    }


def _source_and_reference_bindings(repo_root: Path, reference_v3: Mapping[str, Any], reference_v4: Mapping[str, Any]) -> dict[str, Any]:
    e2_root = repo_root / E2_RELATIVE
    v3_path = repo_root / REFERENCE_V3_RELATIVE
    v4_path = e2_root / "reference_v4.json"
    extension_path = e2_root / "reference_v4_extension.json"
    acceptance_path = e2_root / "reference_v4_extension_acceptance.json"
    extension = _read_json(extension_path)
    acceptance = _read_json(acceptance_path)
    extension_digest = sha256_file(extension_path)
    accepted_revision = str(acceptance.get("accepted_revision_sha256", ""))
    extension_pairs = [
        pair
        for pair in reference_v4.get("pairs", [])
        if str(pair.get("id", "")) not in {str(row.get("id", "")) for row in reference_v3.get("pairs", [])}
    ]
    if len(extension_pairs) != len(extension.get("pairs", [])):
        raise ValueError("reference v4 extension pair count does not match the merged reference")
    v4_by_id = {str(pair["id"]): pair for pair in reference_v4.get("pairs", [])}
    for pair in extension.get("pairs", []):
        merged = v4_by_id.get(str(pair["id"]))
        if merged is None or (
            _reference_edge(pair), pair.get("role"), pair.get("directness")
        ) != (
            _reference_edge(merged), merged.get("role"), merged.get("directness")
        ):
            raise ValueError(f"reference v4 extension label mismatch: {pair.get('id')}")
    if accepted_revision and not extension_digest.lower().startswith(accepted_revision.lower()):
        raise ValueError("reference extension hash does not match the acceptance record")
    if acceptance.get("verdict") != "accepted" or any(
        item.get("result") != "pass" for item in acceptance.get("checks_run_by_the_lead", [])
    ):
        raise ValueError("extension acceptance record is not fully passing")
    extension_roles = Counter(str(pair.get("role")) for pair in extension_pairs)
    extension_directness = Counter(
        str(pair.get("directness") or "not_applicable") for pair in extension_pairs
    )
    assistant_pairs = [
        {
            "pair_id": str(pair["id"]),
            "target_ku": str(pair["target_ku"]),
            "prerequisite_ku": str(pair["prerequisite_ku"]),
            "role": str(pair["role"]),
            "directness": pair.get("directness"),
            "provenance": str(pair.get("provenance")),
        }
        for pair in extension_pairs
    ]
    checks = acceptance.get("checks_run_by_the_lead", [])
    open_questions = [
        {
            key: question.get(key)
            for key in ("rank", "pairs", "question", "changes_denominator", "status")
            if key in question
        }
        for question in reference_v4.get("open_questions", [])
    ]
    return {
        "references": {
            "reference_v3": {"path": v3_path.relative_to(repo_root).as_posix(), "sha256": sha256_file(v3_path)},
            "reference_v4": {"path": v4_path.relative_to(repo_root).as_posix(), "sha256": sha256_file(v4_path)},
        },
        "extension_review": {
            "extension_path": extension_path.relative_to(repo_root).as_posix(),
            "extension_sha256": extension_digest,
            "acceptance_path": acceptance_path.relative_to(repo_root).as_posix(),
            "acceptance_sha256": sha256_file(acceptance_path),
            "accepted_revision_hash_prefix_matches": bool(
                accepted_revision and extension_digest.lower().startswith(accepted_revision.lower())
            ),
            "verifier": acceptance.get("verifier"),
            "verdict": acceptance.get("verdict"),
            "pair_count": len(extension_pairs),
            "assistant_source_adjudication_pair_count": sum(
                pair.get("provenance") == "assistant_source_adjudication" for pair in extension_pairs
            ),
            "roles": dict(sorted(extension_roles.items())),
            "directness": dict(sorted(extension_directness.items())),
            "lead_check_results": [
                {"check": item.get("check"), "result": item.get("result")}
                for item in checks
            ],
            "assistant_adjudicated_pairs": assistant_pairs,
        },
        "open_questions": open_questions,
    }


def build_report(repo_root: Path | str) -> dict[str, Any]:
    """Read frozen local artifacts and return the complete offline score report."""
    root = Path(repo_root).resolve()
    v3_path = root / REFERENCE_V3_RELATIVE
    v4_path = root / E2_RELATIVE / "reference_v4.json"
    v3 = _read_json(v3_path)
    v4 = _read_json(v4_path)
    consistency = validate_reference_pair_consistency(v3, v4)
    e2_root = root / E2_RELATIVE
    target_selection = _read_json(e2_root / "target_selection.json")
    selected_target_ids = {str(row["unit_id"]) for row in target_selection.get("targets", [])}
    v4_target_ids = {str(row["unit_id"]) for row in v4.get("targets", [])}
    if len(selected_target_ids) != 9 or selected_target_ids != v4_target_ids:
        raise ValueError("target selection and nine-target reference v4 do not match")

    runs = [_load_run(root, arm, repeat) for arm, repeat in EXPECTED_RUNS]
    for run in runs:
        for candidate_group in run["candidate_sets"].values():
            if any(edge[0] not in selected_target_ids for edge in candidate_group):
                raise ValueError(f"candidate learning goal outside frozen nine-target selection: {run['arm']}/r{run['repeat']}")
        for row in run["predictions"]:
            if _model_edge(row)[0] not in selected_target_ids:
                raise ValueError(f"prediction learning goal outside frozen nine-target selection: {run['arm']}/r{run['repeat']}")

    scoring: dict[str, Any] = {}
    reference_rows: dict[str, list[dict[str, Any]]] = {}
    for ref_key, reference in (("original_four_target_v3", v3), ("expanded_nine_target_v4", v4)):
        scored_rounds = []
        for run in runs:
            scored = _score_reference(run, reference)
            scored.update(
                {
                    "arm": run["arm"],
                    "repeat": run["repeat"],
                    "model": run["model"],
                    "provider": run["provider"],
                    "run_counts": run["run_counts"],
                    "provenance": run["provenance"],
                }
            )
            scored_rounds.append(scored)
        reference_rows[ref_key] = scored_rounds
        scoring[ref_key] = {
            "reference_path": (
                REFERENCE_V3_RELATIVE.as_posix()
                if ref_key == "original_four_target_v3"
                else (E2_RELATIVE / "reference_v4.json").as_posix()
            ),
            "reference_pair_count": len(reference.get("pairs", [])),
            "target_count": len(reference.get("targets", [])),
            "rounds": scored_rounds,
            "aggregate": _aggregate_reference(scored_rounds),
        }

    deviation_records = []
    for run in runs:
        deviation = run["manifest"].get("output_token_deviation") or {}
        deviation_records.append(
            {
                "arm": run["arm"],
                "repeat": run["repeat"],
                "configured_max_tokens": run["manifest"].get("max_tokens"),
                "plan_value": deviation.get("plan_value"),
                "used_value": deviation.get("used_value"),
                "reason": deviation.get("reason"),
            }
        )

    provenance = _source_and_reference_bindings(root, v3, v4)
    projection_paths = {run["provenance"]["source"]["path"] for run in runs}
    projection_hashes = {run["provenance"]["source"]["sha256"] for run in runs}
    if len(projection_paths) != 1 or len(projection_hashes) != 1:
        raise ValueError("the four repeats do not share one frozen projection source")
    provenance["shared_source"] = {
        "path": next(iter(projection_paths)),
        "sha256": next(iter(projection_hashes)),
    }
    provenance["agent_configuration"] = {
        "model": "gpt-6-luna",
        "reasoning_effort": "max",
        "purpose": "offline code subtask provenance; not an experiment model call",
    }
    provenance["execution_boundary"] = {
        "offline_only": True,
        "research_api_called": False,
        "extraction_started": False,
        "existing_gold_or_experiment_artifacts_modified": False,
    }

    report = {
        "schema": "maf_kg.www_supplement.prerequisite_closeout.v1",
        "analysis": "offline scoring of the four actual nine-target E2 runs",
        "edge_direction": {
            "model": "source_ku (learning goal) depends_on target_ku (prerequisite)",
            "reference": "target_ku (learning goal) depends_on prerequisite_ku",
            "normalization": "source_ku=reference.target_ku; model.target_ku=reference.prerequisite_ku",
        },
        "scope_and_limits": [
            "原四目标 reference_v3 是从同一组九目标实跑中过滤目标后的离线评分，不构成独立四目标实验。",
            "未决参考对（role=uncertain）不计入 verdict 指标、角色混淆矩阵或必需边分母。",
            "精确率仅针对命中已枚举且有确定 verdict 的参考对，不代表全目录或全教材精确率。",
            "参考未列的接受边（含间接边）单独列为未裁定，不暗中计作 FP。",
            "有限参考不宣称穷尽，也不等同于全部人工 Gold；v4 扩展含助手来源判定和未决问题。",
        ],
        "reference_consistency": consistency,
        "scoring": scoring,
        "provenance": provenance,
        "token_budget_deviation": {
            "runs": deviation_records,
            "interpretation": (
                "记录显示，针对 86 KB proposer payload 的一次 12,000-token 尝试把额度耗尽于 reasoning，"
                "并以 finish_reason=length 返回空内容；后续这些实际运行在 32,000 完成。"
                "这是一次有限观察，不能推出所有 12,000-token 尝试必然失败。"
            ),
        },
    }
    return report


def _fmt(value: Any) -> str:
    return "N/A" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# WWW 补充材料先修关系离线收口",
        "",
        "> 方向：学习目标 A `depends_on` 先修 B。模型 `source_ku` 是 A、`target_ku` 是 B；参考 `target_ku` 是 A、`prerequisite_ku` 是 B。",
        "",
        "## 范围与边界",
        "",
    ]
    lines.extend(f"- {note}" for note in report["scope_and_limits"])
    lines.extend(
        [
            "- TP/FP 仅依据实际非弃判参考标签；未列于有限参考的接受边另列，不充当负例。",
            "",
            "## 参考一致性与扩展审阅",
            "",
            f"- v3/v4 已核对 `{report['reference_consistency']['v3_pairs_checked']}` 对沿用边的端点、role、directness；一致性检查通过。",
            f"- v4 扩展 `{report['provenance']['extension_review']['pair_count']}` 对中，`assistant_source_adjudication` 有 `{report['provenance']['extension_review']['assistant_source_adjudication_pair_count']}` 对；独立验收记录 verdict 为 `{report['provenance']['extension_review']['verdict']}`。这不等于完整人工 Gold。",
            "- 扩展待决问题（原样保留，不在本任务中裁决）：",
        ]
    )
    for question in report["provenance"]["open_questions"]:
        pairs = ", ".join(question.get("pairs") or []) or "无 pair ID"
        denominator = question.get("changes_denominator")
        lines.append(
            f"  - #{question.get('rank')} ({pairs}; changes_denominator={denominator}): "
            f"{question.get('question')} [{question.get('status')}]"
        )
    lines.extend(["", "## 每轮结果", ""])
    for ref_key, title in (
        ("original_four_target_v3", "原四目标 reference_v3（九目标运行筛选四目标）"),
        ("expanded_nine_target_v4", "扩展九目标 reference_v4"),
    ):
        section = report["scoring"][ref_key]
        lines.extend(
            [
                f"### {title}",
                "",
                "| Arm / round | Direct TP/FP/FN | Direct P/R/F1 | All-required TP/FP/FN | All-required P/R/F1 | Verdict coverage | Candidate recall: projection / semantic additions / merged | Support / indirect-to-direct upgrades | Outside-reference accepted D/I |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in section["rounds"]:
            direct = row["direct_required"]
            required = row["all_required"]
            dcounts = direct["counts"]
            rcounts = required["counts"]
            recall = row["candidate_recall"]
            confusion = row["role_confusion"]
            lines.append(
                "| {arm} r{repeat} | {dtp}/{dfp}/{dfn} | {dp}/{dr}/{df} | {rtp}/{rfp}/{rfn} | {rp}/{rr}/{rf} | {covered}/{decisive} ({coverage}) | {proj} / {sem} / {merged} | {support} / {indirect} | {outside} ({outside_direct}/{outside_indirect}) |".format(
                    arm=row["arm"],
                    repeat=row["repeat"],
                    dtp=dcounts["tp"],
                    dfp=dcounts["fp"],
                    dfn=dcounts["fn"],
                    dp=_fmt(direct["precision"]),
                    dr=_fmt(direct["recall"]),
                    df=_fmt(direct["f1"]),
                    rtp=rcounts["tp"],
                    rfp=rcounts["fp"],
                    rfn=rcounts["fn"],
                    rp=_fmt(required["precision"]),
                    rr=_fmt(required["recall"]),
                    rf=_fmt(required["f1"]),
                    covered=row["actual_verdict_coverage"]["covered_decisive_pairs"],
                    decisive=row["actual_verdict_coverage"]["decisive_reference_pairs"],
                    coverage=_fmt(row["actual_verdict_coverage"]["ratio"]),
                    proj=_fmt(recall["projection"]["recall"]),
                    sem=_fmt(recall["semantic_additions"]["recall"]),
                    merged=_fmt(recall["merged"]["recall"]),
                    support=confusion["support_upgrade_error_count"],
                    indirect=confusion["indirect_to_direct_upgrade_error_count"],
                    outside=row["accepted_required_edges_outside_reference"]["count"],
                    outside_direct=row["accepted_required_edges_outside_reference"]["direct_count"],
                    outside_indirect=row["accepted_required_edges_outside_reference"]["indirect_count"],
                )
            )
        aggregate = section["aggregate"]["macro_mean_and_range_across_runs"]
        lines.extend(
            [
                "",
                "宏均值（四个 run-observations）/范围：",
                "",
                "| 指标 | 均值 | 最小 | 最大 | 范围 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name in (
            "direct_precision",
            "direct_recall",
            "direct_f1",
            "all_required_precision",
            "all_required_recall",
            "all_required_f1",
            "actual_verdict_coverage",
            "projection_candidate_recall",
            "semantic_addition_candidate_recall",
            "merged_candidate_recall",
        ):
            item = aggregate[name]
            lines.append(
                f"| {name} | {_fmt(item['mean'])} | {_fmt(item['min'])} | {_fmt(item['max'])} | {_fmt(item['range'])} |"
            )
        lines.extend(["", "逐轮角色混淆矩阵（仅已覆盖的非 uncertain 参考 pair；行=参考，列=预测）：", ""])
        for row in section["rounds"]:
            matrix = row["role_confusion"]["rows_reference_role_columns_predicted_role"]
            cells = [
                f"{gold}>{pred}={count}"
                for gold, columns in matrix.items()
                for pred, count in columns.items()
                if count
            ]
            lines.append(f"- {row['arm']} r{row['repeat']}: " + ("; ".join(cells) if cells else "无已覆盖 pair"))
        lines.append("")

    lines.extend(["## SHA-256 绑定", ""])
    shared_source = report["provenance"]["shared_source"]
    lines.append(f"- source `{shared_source['path']}`: `{shared_source['sha256']}`")
    for name, binding in report["provenance"]["references"].items():
        lines.append(f"- {name} `{binding['path']}`: `{binding['sha256']}`")
    lines.append("")
    lines.append("每轮 prediction、review batches、run config、candidate 与 accepted-direct 原始文件的完整路径和 SHA-256 均在 JSON `scoring.*.rounds[].provenance` 中；review 另有逐批与聚合绑定。")
    lines.extend(["", "## Token 上限偏离", ""])
    lines.append(report["token_budget_deviation"]["interpretation"])
    lines.append("")
    for item in report["token_budget_deviation"]["runs"]:
        lines.append(
            f"- {item['arm']} r{item['repeat']}: configured/used `{item['configured_max_tokens']}`; "
            f"plan `{item['plan_value']}`; reason: {item['reason']}"
        )
    lines.append("")
    lines.append("JSON 同时逐条列出 TP/FP/FN、未裁定接受边（含 direct/indirect）、候选召回命中/漏失、support 与 indirect 升级错误及 extension 助手标注 ledger。")
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], repo_root: Path | str) -> tuple[Path, Path]:
    root = Path(repo_root).resolve()
    output_dir = root / OUTPUT_RELATIVE
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_NAME
    markdown_path = output_dir / MARKDOWN_NAME
    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with markdown_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(report))
    return json_path, markdown_path


def main(repo_root: Path | str | None = None) -> tuple[Path, Path]:
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]
    report = build_report(root)
    return write_report(report, root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    json_output, markdown_output = main(args.repo_root)
    print(json_output)
    print(markdown_output)
