"""Offline tests for the E1 layered scorer.

These lock down the rules that are easy to get wrong and expensive to get wrong,
because a wrong denominator silently changes a headline number.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "e1_score", WORKSPACE / "analysis" / "www_supplement_e1_score.py")
assert SPEC and SPEC.loader
score_mod = importlib.util.module_from_spec(SPEC)
sys.modules["e1_score"] = score_mod
SPEC.loader.exec_module(score_mod)


def reference():
    nodes = [
        {"id": "N1", "type": "Claim", "status": "M", "tier": "core", "name": "c1"},
        {"id": "N2", "type": "Claim", "status": "M", "tier": "detail", "name": "c2"},
        {"id": "N3", "type": "Concept", "status": "M", "tier": "core", "name": "c3"},
        {"id": "N4", "type": "Claim", "status": "O", "tier": "detail", "name": "opt"},
    ]
    relations = [{"id": f"R{i}", "type": "involves", "start": "N1", "end": "N3"}
                 for i in range(1, 5)]
    return {"nodes": nodes, "relations": relations}


def items(rows):
    return [{"run": "runA", **r} for r in rows]


def rels(rows):
    return [{"run": "runA", **r} for r in rows]


# ---------------------------------------------------------------- content

def test_complete_coverage_counts_only_full():
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full"},
        {"item_id": "N2", "coverage": "partial"},
        {"item_id": "N3", "coverage": "name_only"},
    ]), [])
    c = m["runs"]["runA"]["complete_required"]
    assert c["numerator"] == 1 and c["denominator"] == 3
    assert c["value"] == pytest.approx(1 / 3, abs=1e-6)


def test_recognized_coverage_includes_partial_and_name_only():
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full"},
        {"item_id": "N2", "coverage": "partial"},
        {"item_id": "N3", "coverage": "name_only"},
    ]), [])
    r = m["runs"]["runA"]["recognized_required"]
    assert r["numerator"] == 3 and r["denominator"] == 3


def test_optional_items_are_not_in_the_required_denominator():
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full"},
        {"item_id": "N2", "coverage": "full"},
        {"item_id": "N3", "coverage": "full"},
        {"item_id": "N4", "coverage": "missing"},
    ]), [])
    assert m["runs"]["runA"]["complete_required"]["denominator"] == 3
    assert m["runs"]["runA"]["complete_required"]["value"] == 1.0


def test_core_and_detail_split():
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full"},
        {"item_id": "N2", "coverage": "missing"},
        {"item_id": "N3", "coverage": "full"},
    ]), [])
    row = m["runs"]["runA"]
    assert row["complete_core"] == {"numerator": 2, "denominator": 2, "value": 1.0}
    assert row["complete_detail"] == {"numerator": 0, "denominator": 1, "value": 0.0}


def test_unjudged_item_counts_as_not_covered_not_as_excluded():
    m = score_mod.score(reference(), items([{"item_id": "N1", "coverage": "full"}]), [])
    c = m["runs"]["runA"]["complete_required"]
    assert c["numerator"] == 1 and c["denominator"] == 3


# ------------------------------------------------------------- conditions

def test_missing_claim_counts_as_conditions_not_retained():
    """The whole point of the contract change: a never-delivered Claim is a miss."""
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full", "conditions_complete": True},
        {"item_id": "N2", "coverage": "missing", "conditions_complete": False},
        {"item_id": "N3", "coverage": "full", "conditions_complete": True},
    ]), [])
    cond = m["runs"]["runA"]["condition_retention"]
    assert cond["denominator"] == 2, "denominator is every required Claim"
    assert cond["numerator"] == 1
    assert cond["claims_not_delivered"] == 1


def test_conditions_scored_only_over_delivered_claims_is_wrong():
    """Guard against the earlier policy's gap: it would have scored 1/1 = 1.0."""
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full", "conditions_complete": True},
        {"item_id": "N2", "coverage": "missing", "conditions_complete": True},
        {"item_id": "N3", "coverage": "full", "conditions_complete": True},
    ]), [])
    cond = m["runs"]["runA"]["condition_retention"]
    assert cond["value"] == 0.5, "a missing Claim cannot earn condition credit"


def test_delivered_claim_without_conditions_is_counted_separately():
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "partial", "conditions_complete": False},
        {"item_id": "N2", "coverage": "full", "conditions_complete": True},
        {"item_id": "N3", "coverage": "full", "conditions_complete": True},
    ]), [])
    cond = m["runs"]["runA"]["condition_retention"]
    assert cond["numerator"] == 1
    assert cond["claims_delivered_without_complete_conditions"] == 1


# --------------------------------------------------------------- relations

def test_semantic_full_and_partial_are_separate():
    m = score_mod.score(reference(), [], rels([
        {"relation_id": "R1", "semantic": "full"},
        {"relation_id": "R2", "semantic": "partial"},
        {"relation_id": "R3", "semantic": "mentioned_only"},
        {"relation_id": "R4", "semantic": "not_covered"},
    ]))
    s = m["runs"]["runA"]["relation_semantic"]
    assert s["full"] == 1 and s["partial"] == 1
    assert s["full_plus_partial"] == 2
    assert s["denominator"] == 4


def test_strict_prints_both_denominators():
    m = score_mod.score(reference(), [], rels([
        {"relation_id": "R1", "strict": "conforms"},
        {"relation_id": "R2", "strict": "not_comparable"},
        {"relation_id": "R3", "strict": "absent"},
        {"relation_id": "R4", "strict": "type_mismatch"},
    ]))
    st = m["runs"]["runA"]["relation_strict"]
    assert st["conforms_over_all_relations"] == {
        "numerator": 1, "denominator": 4, "value": 0.25}
    assert st["conforms_over_comparable"] == {
        "numerator": 1, "denominator": 2, "value": 0.5}
    assert st["comparable_denominator"] == 2


def test_edc_style_all_not_comparable_gives_no_comparable_value():
    m = score_mod.score(reference(), [], rels([
        {"relation_id": f"R{i}", "strict": "not_comparable"} for i in range(1, 5)]))
    st = m["runs"]["runA"]["relation_strict"]
    assert st["comparable_denominator"] == 0
    assert st["conforms_over_comparable"]["value"] is None


def test_native_signature_is_its_own_column():
    m = score_mod.score(reference(), [], rels([
        {"relation_id": "R1", "strict": "conforms", "native_signature": "valid"},
        {"relation_id": "R2", "strict": "type_mismatch", "native_signature": "out_of_signature"},
        {"relation_id": "R3", "strict": "not_comparable", "native_signature": "valid"},
        {"relation_id": "R4", "strict": "absent"},
    ]))
    n = m["runs"]["runA"]["native_signature_validity"]
    assert n["judged"] == 3
    assert n["counts"]["valid"] == 2
    assert n["counts"]["out_of_signature"] == 1


# ------------------------------------------------------- verdict coverage

def test_verdict_coverage_uses_the_intersection_not_one_minus_abstention():
    """The earlier bug: (determinate - abstentions)/determinate credited never-judged pairs.

    27 determinate pairs, 9 actually judged, and ZERO abstentions among the judged.
    The correct value is 9/27. A "1 minus the abstention rate" reading gives
    (27-0)/27 = 1.0, so this case separates the two formulas.
    """
    determinate, judged_and_determinate, abstentions = 27, 9, 0
    v = score_mod.verdict_coverage(determinate, judged_and_determinate)
    assert v["value"] == pytest.approx(9 / 27, abs=1e-6)
    naive = (determinate - abstentions) / determinate
    assert naive == 1.0
    assert v["value"] != pytest.approx(naive, abs=1e-6), (
        "never-judged pairs must not be credited as covered")


def test_verdict_coverage_matches_the_recorded_real_pipeline_values():
    """The two numbers the earlier round got wrong: 9/27 and 6/27."""
    assert score_mod.verdict_coverage(27, 9)["value"] == pytest.approx(1 / 3, abs=1e-6)
    assert score_mod.verdict_coverage(27, 6)["value"] == pytest.approx(2 / 9, abs=1e-6)


def test_verdict_coverage_zero_determinate_is_none():
    assert score_mod.verdict_coverage(0, 0)["value"] is None


# ------------------------------------------------------------------ shape

def test_runs_are_kept_separate():
    ref = reference()
    it = items([{"item_id": "N1", "coverage": "full"},
                {"item_id": "N2", "coverage": "full"},
                {"item_id": "N3", "coverage": "full"}])
    it.append({"run": "runB", "item_id": "N1", "coverage": "missing"})
    m = score_mod.score(ref, it, [])
    assert m["runs"]["runA"]["complete_required"]["value"] == 1.0
    assert m["runs"]["runB"]["complete_required"]["value"] == 0.0


def test_markdown_renders_every_run():
    m = score_mod.score(reference(), items([
        {"item_id": "N1", "coverage": "full", "conditions_complete": True}]), [])
    md = score_mod.markdown(m)
    assert "runA" in md and "条件保留" in md


def test_events_and_cost_are_carried_through():
    m = score_mod.score(reference(), items([{"item_id": "N1", "coverage": "full"}]), [],
                        {"runA": {"duplicate_repetitions": 3, "false_merge_events": 2,
                                  "cost": {"requests": 32, "tokens": 560608},
                                  "completion": "completed"}})
    row = m["runs"]["runA"]
    assert row["output_events"]["duplicate_repetitions"] == 3
    assert row["cost"]["tokens"] == 560608
    assert row["completion"] == "completed"
