import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_ku_prerequisite_evaluation import (
    depends_on_to_prerequisite_of,
    graph_diagnostics,
    main,
    score_evaluation,
    transitive_closure,
)


def annotation(prerequisite, target, role="required", directness="direct", provenance="draft"):
    row = {
        "prerequisite_ku": prerequisite,
        "target_ku": target,
        "role": role,
        "provenance": provenance,
    }
    if role == "required":
        row["directness"] = directness
    return row


def test_direction_conversion_and_candidate_recall():
    assert depends_on_to_prerequisite_of({"source_ku": "B", "target_ku": "A"}) == ("A", "B")
    result = score_evaluation({
        "endpoints": ["A", "B"],
        "candidates": [{"source_ku": "B", "target_ku": "A"}],
        "predictions": {"verdicts": [{"source_ku": "B", "target_ku": "A", "verdict": "accept"}]},
        "reference": [annotation("A", "B", provenance="human")],
    })
    assert result["candidate_direct_recall"]["hits"] == 1
    assert result["end_to_end_direct"]["tp"] == 1
    assert result["reachability"]["metrics"]["tp"] == 1


def test_missed_candidate_remains_end_to_end_false_negative():
    result = score_evaluation({
        "endpoints": ["A", "B"],
        "candidates": [],
        "predictions": {"depends_on": []},
        "reference": [annotation("A", "B")],
    })
    assert result["candidate_direct_recall"] == {
        "hits": 0,
        "denominator": 1,
        "recall": 0.0,
        "scope": "Gold required-direct pairs present in the raw candidate pool",
    }
    assert result["end_to_end_direct"]["fn"] == 1
    assert result["end_to_end_direct"]["recall"] == 0.0


def test_uncertain_and_unlabeled_pairs_are_not_negative_examples_or_gold_paths():
    result = score_evaluation({
        "endpoints": ["A", "B", "C"],
        "candidates": [],
        "predictions": {"depends_on": [
            {"source_ku": "C", "target_ku": "A"},
        ]},
        "reference": [
            annotation("A", "B", role="uncertain"),
            annotation("B", "C", role="uncertain"),
        ],
    })
    assert result["end_to_end_direct"]["fp"] == 0
    assert result["end_to_end_direct"]["precision"] is None
    assert result["reachability"]["gold_closure_edge_count"] == 0
    assert result["reachability"]["metrics"]["f1"] is None
    assert result["reachability"]["unlabeled_predicted_closure_edge_count"] == 1


def test_closed_gold_path_and_indirect_direct_false_accept():
    result = score_evaluation({
        "endpoints": ["A", "B", "C"],
        "candidates": [
            {"source_ku": "B", "target_ku": "A"},
            {"source_ku": "C", "target_ku": "B"},
            {"source_ku": "C", "target_ku": "A"},
        ],
        "predictions": {"verdicts": [
            {"source_ku": "B", "target_ku": "A", "verdict": "accept"},
            {"source_ku": "C", "target_ku": "B", "verdict": "accept"},
            {"source_ku": "C", "target_ku": "A", "verdict": "accept"},
        ]},
        "reference": [
            annotation("A", "B"),
            annotation("B", "C"),
            annotation("A", "C", directness="indirect"),
        ],
    })
    assert result["reachability"]["gold_closure_edge_count"] == 3
    assert result["reachability"]["metrics"]["precision"] == 1.0
    assert result["end_to_end_direct"]["fp"] == 1
    assert result["indirect_false_accepts"]["count"] == 1
    assert result["indirect_false_accepts"]["denominator"] == 1


def test_support_false_acceptance_and_candidate_pool_conditional_scoring():
    result = score_evaluation({
        "endpoints": ["A", "B", "C"],
        "candidates": [
            {"source_ku": "B", "target_ku": "A"},
            {"source_ku": "C", "target_ku": "B"},
        ],
        "predictions": {"verdicts": [
            {"source_ku": "B", "target_ku": "A", "verdict": "accept"},
            {"source_ku": "C", "target_ku": "B", "verdict": "accept"},
        ]},
        "reference": [
            annotation("A", "B"),
            annotation("B", "C", role="proof_support"),
        ],
    })
    assert result["candidate_pool_review"]["requiredness"]["tp"] == 1
    assert result["candidate_pool_review"]["requiredness"]["fp"] == 1
    assert result["support_false_accepts"]["count"] == 1
    assert result["support_false_accepts"]["by_role"]["proof_support"]["count"] == 1


def test_cycles_and_self_loops_are_reported_without_reflexive_closure():
    edges = {("A", "B"), ("B", "A"), ("B", "B")}
    diagnostics = graph_diagnostics(["A", "B", "C"], edges)
    assert diagnostics["self_loop_count"] == 1
    assert diagnostics["reciprocal_pairs"] == [["A", "B"]]
    assert diagnostics["nontrivial_sccs"] == [["A", "B"]]
    assert diagnostics["isolated_nodes"] == ["C"]
    assert diagnostics["weak_component_count"] == 2
    assert diagnostics["is_dag"] is False
    assert diagnostics["transitive_reduction_edge_count"] is None
    assert transitive_closure(["A", "B"], edges) == {("A", "B"), ("B", "A")}


def test_transitive_reduction_is_only_a_diagnostic():
    diagnostics = graph_diagnostics(["A", "B", "C"], {("A", "B"), ("B", "C"), ("A", "C")})
    assert diagnostics["unique_valid_edge_count"] == 3
    assert diagnostics["transitive_reduction_edge_count"] == 2
    assert diagnostics["structurally_redundant_edge_count"] == 1


def test_empty_denominators_are_null_and_graph_diagnostics_accept_empty_graph():
    result = score_evaluation({
        "endpoints": [],
        "candidates": [],
        "predictions": {"depends_on": []},
        "reference": [],
    })
    assert result["candidate_direct_recall"]["recall"] is None
    assert result["candidate_pool_review"]["requiredness"]["precision"] is None
    assert result["end_to_end_direct"]["recall"] is None
    assert result["reachability"]["metrics"]["f1"] is None
    assert result["graph_diagnostics"]["candidates"]["largest_weak_component_ratio"] is None


def test_reference_and_prediction_validation_is_strict():
    base = {
        "endpoints": ["A", "B"],
        "candidates": [],
        "predictions": {"verdicts": []},
        "reference": [],
    }
    duplicate_endpoint = copy.deepcopy(base)
    duplicate_endpoint["endpoints"].append("A")
    with pytest.raises(ValueError, match="Duplicate endpoint"):
        score_evaluation(duplicate_endpoint)

    unknown = copy.deepcopy(base)
    unknown["candidates"] = [{"source_ku": "B", "target_ku": "X"}]
    with pytest.raises(ValueError, match="Unknown endpoint"):
        score_evaluation(unknown)

    invalid_role = copy.deepcopy(base)
    invalid_role["reference"] = [annotation("A", "B", role="maybe")]
    with pytest.raises(ValueError, match="Invalid role"):
        score_evaluation(invalid_role)
    invalid_role["reference"] = [annotation("A", "B", role=["required"])]
    with pytest.raises(ValueError, match="Invalid role"):
        score_evaluation(invalid_role)

    duplicate_reference = copy.deepcopy(base)
    duplicate_reference["reference"] = [annotation("A", "B"), annotation("A", "B")]
    with pytest.raises(ValueError, match="Duplicate reference"):
        score_evaluation(duplicate_reference)

    bad_verdict = copy.deepcopy(base)
    bad_verdict["predictions"] = {
        "verdicts": [{"source_ku": "B", "target_ku": "A", "verdict": "maybe"}]
    }
    with pytest.raises(ValueError, match="Invalid verdict"):
        score_evaluation(bad_verdict)


def test_unknown_edges_are_available_as_low_level_graph_diagnostics():
    diagnostics = graph_diagnostics(["A"], [("A", "X")])
    assert diagnostics["invalid_endpoint_edge_count"] == 1
    assert diagnostics["invalid_endpoint_edges"] == [["A", "X"]]


def test_cli_reads_utf8_json_and_writes_report(tmp_path):
    input_path = tmp_path / "evaluation.json"
    output_path = tmp_path / "scores.json"
    input_path.write_text(json.dumps({
        "endpoints": [{"id": "泰勒"}, {"id": "余项"}],
        "candidates": [{"source_ku": "余项", "target_ku": "泰勒"}],
        "predictions": {"depends_on": [
            {"source_ku": "余项", "target_ku": "泰勒"},
        ]},
        "reference": [annotation("泰勒", "余项", provenance="human")],
    }, ensure_ascii=False), encoding="utf-8")

    assert main([str(input_path), "--output", str(output_path)]) == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["end_to_end_direct"]["tp"] == 1
    assert "not expert-Gold" in report["caveat"]
