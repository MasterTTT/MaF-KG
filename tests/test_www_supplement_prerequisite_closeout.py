from __future__ import annotations

import json
from pathlib import Path

from analysis.www_supplement_prerequisite_closeout import (
    EXPECTED_RUNS,
    JSON_NAME,
    MARKDOWN_NAME,
    OUTPUT_RELATIVE,
    binary_metrics,
    build_report,
    main,
    _model_edge,
    _reference_edge,
    validate_reference_pair_consistency,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_model_and_reference_edge_fields_normalize_to_learning_goal_depends_on_prerequisite():
    reference_edge = _reference_edge(
        {"target_ku": "goal-A", "prerequisite_ku": "prerequisite-B"}
    )
    model_edge = _model_edge({"source_ku": "goal-A", "target_ku": "prerequisite-B"})
    assert reference_edge == model_edge
    assert f"{model_edge[0]} depends_on {model_edge[1]}" == "goal-A depends_on prerequisite-B"


def test_binary_metrics_excludes_unlisted_and_uncertain_predictions_from_precision():
    gold = {("goal-A", "pre-A"), ("goal-B", "pre-B")}
    decisive = gold | {("goal-C", "pre-C")}
    predicted = {
        ("goal-A", "pre-A"),
        ("goal-C", "pre-C"),
        ("goal-U", "pre-U"),
        ("goal-X", "pre-X"),
    }
    metrics = binary_metrics(predicted, gold, decisive)
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == (1, 1, 1)
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["unadjudicated_edges"] == [
        ("goal-U", "pre-U"),
        ("goal-X", "pre-X"),
    ]


def test_reference_v3_carried_pairs_are_unchanged_in_v4():
    v3_path = REPO_ROOT / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/reference_v3.json"
    v4_path = REPO_ROOT / "evaluation/www_evidence_supplement_2026-10-11/e2_prerequisite/reference_v4.json"
    with v3_path.open("r", encoding="utf-8") as handle:
        v3 = json.load(handle)
    with v4_path.open("r", encoding="utf-8") as handle:
        v4 = json.load(handle)
    result = validate_reference_pair_consistency(v3, v4)
    assert result["consistent"] is True
    assert result["v3_pairs_checked"] == 28
    assert result["v4_pairs"] == 60


def test_real_nine_target_runs_regression_and_hash_bindings():
    report = build_report(REPO_ROOT)
    assert report["reference_consistency"]["consistent"] is True
    assert [
        (row["arm"], row["repeat"])
        for row in report["scoring"]["expanded_nine_target_v4"]["rounds"]
    ] == list(EXPECTED_RUNS)
    assert report["scoring"]["original_four_target_v3"]["target_count"] == 4
    assert report["scoring"]["expanded_nine_target_v4"]["target_count"] == 9
    assert report["scoring"]["original_four_target_v3"]["reference_pair_count"] == 28
    assert report["scoring"]["expanded_nine_target_v4"]["reference_pair_count"] == 60
    expanded_rounds = report["scoring"]["expanded_nine_target_v4"]["rounds"]
    assert [
        (
            row["run_counts"]["prediction_count"],
            row["run_counts"]["required_prediction_count"],
            row["run_counts"]["direct_accepted_count"],
            row["run_counts"]["indirect_required_count"],
            row["run_counts"]["projection_candidate_count"],
            row["run_counts"]["semantic_addition_count"],
            row["run_counts"]["merged_candidate_count"],
        )
        for row in expanded_rounds
    ] == [
        (22, 9, 7, 2, 22, 0, 22),
        (22, 6, 6, 0, 22, 0, 22),
        (25, 9, 9, 0, 22, 3, 25),
        (25, 10, 10, 0, 22, 3, 25),
    ]
    assert [
        (
            row["direct_required"]["counts"]["tp"],
            row["direct_required"]["counts"]["fp"],
            row["direct_required"]["counts"]["fn"],
        )
        for row in expanded_rounds
    ] == [(6, 1, 5), (6, 0, 5), (8, 1, 3), (7, 3, 4)]
    assert [
        (
            row["role_confusion"]["support_upgrade_error_count"],
            row["role_confusion"]["indirect_to_direct_upgrade_error_count"],
        )
        for row in expanded_rounds
    ] == [(1, 0), (0, 0), (1, 0), (2, 0)]
    assert [
        (
            row["accepted_required_edges_outside_reference"]["direct_count"],
            row["accepted_required_edges_outside_reference"]["indirect_count"],
        )
        for row in expanded_rounds
    ] == [(0, 1), (0, 0), (0, 0), (0, 0)]
    assert report["provenance"]["extension_review"]["assistant_source_adjudication_pair_count"] == 32
    assert len(report["provenance"]["open_questions"]) == 7
    assert report["provenance"]["agent_configuration"] == {
        "model": "gpt-6-luna",
        "reasoning_effort": "max",
        "purpose": "offline code subtask provenance; not an experiment model call",
    }
    for reference in report["scoring"].values():
        assert len(reference["rounds"]) == 4
        for run in reference["rounds"]:
            provenance = run["provenance"]
            for key in ("source", "actual_prediction", "review", "config"):
                assert len(provenance[key].get("sha256", provenance[key].get("aggregate_sha256", ""))) == 64
            assert provenance["source"]["manifest_sha256_match"] is True
            assert len(provenance["review"]["actual_verdict_artifact"]["sha256"]) == 64


def test_real_entrypoint_writes_only_the_two_closeout_artifacts():
    json_path, markdown_path = main(REPO_ROOT)
    expected_dir = REPO_ROOT / OUTPUT_RELATIVE
    assert json_path == expected_dir / JSON_NAME
    assert markdown_path == expected_dir / MARKDOWN_NAME
    with json_path.open("r", encoding="utf-8") as handle:
        report = json.load(handle)
    markdown = markdown_path.read_text(encoding="utf-8")
    assert report["analysis"] == "offline scoring of the four actual nine-target E2 runs"
    assert "A `depends_on`" in markdown
    assert "不构成独立四目标实验" in markdown
    assert "不能推出所有 12,000-token 尝试必然失败" in markdown
