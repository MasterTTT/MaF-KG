import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_external_baseline_reviews import ratios, validate


def fixture():
    packet = {"label": "P01", "items": [{"item_id": "N001"}],
              "reference": {"nodes": [{"id": "A"}], "relations": []}}
    review = {"label": "P01", "reviewer_provenance": "AI-assisted source review",
              "node_coverage": [{"reference_id": "A", "status": "full", "item_ids": ["N001"],
                                 "conditions_complete": True, "reason": "Complete definition in output"}],
              "relation_coverage": [],
              "prediction_support": [{"item_id": "N001", "status": "yes", "scope_admitted": True,
                                      "reason": "Definition explicitly stated in cited source"}]}
    return packet, review


def test_explicit_complete_review_validates():
    validate(*fixture())


def test_missing_condition_not_full_coverage():
    packet, review = fixture()
    review["node_coverage"][0]["conditions_complete"] = False
    with pytest.raises(ValueError, match="Incomplete conditions"):
        validate(packet, review)


def test_no_silent_unreviewed_predictions():
    packet, review = fixture()
    review["prediction_support"] = []
    with pytest.raises(ValueError, match="incomplete"):
        validate(packet, review)


def test_no_repeated_reference_judgments():
    packet, review = fixture()
    review["node_coverage"].append(copy.deepcopy(review["node_coverage"][0]))
    with pytest.raises(ValueError, match="repeated"):
        validate(packet, review)


def test_full_claim_requires_explicit_condition_judgment():
    packet, review = fixture()
    packet["reference"]["nodes"][0]["type"] = "Claim"
    review["node_coverage"][0]["conditions_complete"] = None
    with pytest.raises(ValueError, match="explicit condition"):
        validate(packet, review)


def test_absent_denominators_are_not_zero_quality():
    assert all(value is None for value in ratios({}).values())


def test_name_only_is_recognition_not_full_semantic_recall():
    result = ratios({"required": 4, "required_full": 1, "required_partial": 1,
                     "required_name_only": 1, "core_required": 2, "core_full": 1})
    assert result["full_required_recall"] == 0.25
    assert result["recognized_required_recall"] == 0.75
    assert result["full_core_recall"] == 0.5
    assert "f1" not in result
