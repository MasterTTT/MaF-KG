"""Offline tests for the unified scoped scoring and aggregate entry points."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "analysis"))

import www_supplement_e1_aggregate as e1_aggregate
import www_supplement_e1e3_score as e1e3_score
from www_supplement_scope_score import score_scoped


def reference():
    nodes = [
        {"id": "A1", "type": "Claim", "status": "M", "tier": "core",
         "section": "docA", "name": "claim A1"},
        {"id": "A2", "type": "Claim", "status": "M", "tier": "detail",
         "section": "docA", "name": "claim A2"},
        {"id": "A3", "type": "Concept", "status": "M", "tier": "core",
         "section": "docA", "name": "concept A3"},
        {"id": "A4", "type": "Concept", "status": "O", "tier": "detail",
         "section": "docA", "name": "optional A4"},
        {"id": "B1", "type": "Claim", "status": "M", "tier": "core",
         "section": "docB", "name": "claim B1"},
        {"id": "B2", "type": "Concept", "status": "O", "tier": "detail",
         "section": "docB", "name": "optional B2"},
    ]
    relations = [
        {"id": "RA", "type": "involves", "start": "A1", "end": "A3"},
        {"id": "RB", "type": "supports", "start": "B1", "end": "B2"},
        {"id": "RAB", "type": "crosses", "start": "A1", "end": "B1"},
    ]
    return {"nodes": nodes, "relations": relations}


def test_run_scope_status_tiers_optionals_and_relation_endpoints():
    mapping = {"A": {"method": "maf", "section": "docA", "repeat": 1}}
    items = [
        {"run": "A", "item_id": "A1", "coverage": "full",
         "conditions_complete": True},
        {"run": "A", "item_id": "A2", "coverage": "missing",
         "conditions_complete": False},
        {"run": "A", "item_id": "A4", "coverage": "full"},
        {"run": "A", "item_id": "B1", "coverage": "full"},
    ]
    relations = [
        {"run": "A", "relation_id": "RA", "semantic": "full", "strict": "conforms"},
        {"run": "A", "relation_id": "RAB", "semantic": "full", "strict": "conforms"},
    ]

    result = score_scoped(reference(), items, relations, mapping)
    row = result["per_run"]["A"]

    assert row["pool_items"] == 4
    assert row["pool_relations"] == 1
    assert row["scope_diagnostics"]["out_of_scope_item_rows_ignored"] == 1
    assert row["scope_diagnostics"]["out_of_scope_relation_rows_ignored"] == 1
    assert row["reference_by_status"]["M"]["count"] == 3
    assert row["complete"]["numerator"] == 1
    assert row["complete"]["denominator"] == 3
    assert row["complete"]["unknown_count"] == 1
    assert row["complete"]["value"] is None
    assert row["complete"]["lower_bound"] == pytest.approx(1 / 3)
    assert row["complete"]["upper_bound"] == pytest.approx(2 / 3)
    assert row["complete_core"]["denominator"] == 2
    assert row["complete_detail"]["numerator"] == 0
    assert row["complete_detail"]["denominator"] == 1
    assert row["optionals"]["complete"]["numerator"] == 1
    assert row["optionals"]["complete"]["denominator"] == 1
    assert result["optionals"]["per_run"]["A"] == row["optionals"]


def test_claim_null_is_unknown_but_explicit_missing_is_failure():
    ref = reference()
    mapping = {"A": {"method": "maf", "section": "docA", "repeat": 1}}
    items = [
        {"run": "A", "item_id": "A1", "coverage": "full",
         "conditions_complete": None},
        {"run": "A", "item_id": "A2", "coverage": "full",
         "conditions_complete": False},
        {"run": "A", "item_id": "A3", "coverage": "missing",
         "conditions_complete": True},
    ]

    condition = score_scoped(ref, items, [], mapping)["per_run"]["A"]["condition_retention"]
    assert condition["denominator"] == 2
    assert condition["numerator"] == 0
    assert condition["value"] is None
    assert condition["lower_bound"] == 0
    assert condition["upper_bound"] == pytest.approx(0.5)
    assert condition["unknown_count"] == 1
    assert condition["claims_with_unjudged_conditions"] == 1
    assert condition["claims_delivered_without_complete_conditions"] == 1
    assert condition["claims_not_delivered"] == 0

    explicit_missing = score_scoped(
        ref,
        [
            {"run": "A", "item_id": "A1", "coverage": "full",
             "conditions_complete": True},
            {"run": "A", "item_id": "A2", "coverage": "missing",
             "conditions_complete": None},
        ],
        [],
        mapping,
    )["per_run"]["A"]["condition_retention"]
    assert explicit_missing["value"] == 0.5
    assert explicit_missing["claims_not_delivered"] == 1
    assert explicit_missing["unknown_count"] == 0


def test_aggregate_entry_pools_methods_documents_and_equal_weight_macro():
    ref = reference()
    mapping = {
        "A1": {"method": "maf", "section": "docA", "repeat": 1},
        "B1": {"method": "direct", "section": "docB", "repeat": 1},
        "B2": {"method": "maf", "section": "docB", "repeat": 2},
    }
    items = []
    for item_id in ("A1", "A2", "A3"):
        items.append({"run": "A1", "item_id": item_id, "coverage": "full",
                      "conditions_complete": True if item_id == "A1" else None})
    items.extend([
        {"run": "B1", "item_id": "B1", "coverage": "missing"},
        {"run": "B2", "item_id": "B1", "coverage": "full",
         "conditions_complete": True},
    ])
    relations = [
        {"run": "A1", "relation_id": "RA", "semantic": "full", "strict": "conforms"},
        {"run": "B1", "relation_id": "RB", "semantic": "partial", "strict": "conforms"},
        {"run": "B2", "relation_id": "RB", "semantic": "uncertain", "strict": "uncertain"},
    ]

    output = e1_aggregate.aggregate_reviews(ref, items, relations, mapping)

    assert output["per_run"]["A1"]["complete"]["value"] == 1.0
    assert output["per_run"]["B1"]["complete"]["value"] == 0.0
    assert output["per_method"]["maf"]["runs"] == 2
    assert output["per_method"]["maf"]["complete_required"]["denominator"] == 4
    assert output["per_method"]["maf"]["complete_required"]["numerator"] == 4
    assert output["per_document"]["docB"]["runs"] == 2
    assert output["per_document"]["docB"]["complete_required"]["value"] == 0.5
    assert output["macro"]["complete"]["value"] == pytest.approx(2 / 3)
    assert output["pooled"]["complete_required"]["value"] == 0.8
    assert output["per_run"]["B2"]["relation_strict"][
        "conforms_over_comparable"]["value"] is None
    assert output["per_run"]["B2"]["relation_strict"][
        "conforms_over_comparable"]["upper_bound"] == 1.0
    assert output["per_method"]["maf"]["condition_retention"]["value"] is None
    assert output["per_method"]["maf"]["condition_retention"]["unknown_count"] == 1
    assert output["optionals"]["per_document"]["docA"]["count"] == 1
    assert output["schema"] == "maf_kg.www_supplement.e1_metrics.v1"


def test_prefix_mapping_retains_future_i3_run_and_uses_common_pool():
    mapping = {
        "R01": {"method": "maf", "section": "input_I1", "repeat": 1},
        "R08": {"method": "maf", "section": "input_I3", "repeat": 3},
    }
    prefix = e1e3_score.prefix_mapping(mapping)
    assert set(prefix) == {"R01", "R08"}
    assert prefix["R08"]["section"] == "input_I3"
    assert prefix["R08"]["reference_section"] == "ch4_s1"

    common = {"nodes": [
        {"id": "P1", "type": "Claim", "status": "M", "tier": "core",
         "section": "ch4_s1"},
    ], "relations": []}
    result = e1e3_score.score(
        common,
        [{"run": "R08", "item_id": "P1", "coverage": "full",
          "conditions_complete": True}],
        [],
        prefix,
        "prefix I3 test",
    )
    assert "R08" in result["per_run"]
    assert result["per_run"]["R08"]["complete"]["value"] == 1.0


def test_duplicate_run_item_and_relation_keys_are_rejected():
    ref = reference()
    mapping = {"A": {"method": "maf", "section": "docA", "repeat": 1}}
    duplicate_items = [
        {"run": "A", "item_id": "A1", "coverage": "full"},
        {"run": "A", "item_id": "A1", "coverage": "missing"},
    ]
    with pytest.raises(ValueError, match="duplicate item_rows key"):
        score_scoped(ref, duplicate_items, [], mapping)

    duplicate_relations = [
        {"run": "A", "relation_id": "RA", "semantic": "full"},
        {"run": "A", "relation_id": "RA", "semantic": "partial"},
    ]
    with pytest.raises(ValueError, match="duplicate relation_rows key"):
        score_scoped(ref, [], duplicate_relations, mapping)
