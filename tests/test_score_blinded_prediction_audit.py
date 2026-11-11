import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_blinded_prediction_audit import categorical, validate_ratings


def test_uncertainty_is_reported_not_silently_removed():
    rows = [{"support": s} for s in ("supported", "unsupported", "uncertain", "uncertain")]
    result = categorical(rows, "support", "supported", "unsupported")
    assert result["resolved_ratio"] == 0.5 and result["resolved_denominator"] == 2
    assert result["lower_bound"] == 0.25 and result["upper_bound"] == 0.75


def test_conditions_exclude_na_but_keep_uncertain_bounds():
    rows = [{"condition": s} for s in ("complete", "incomplete", "uncertain", "not_applicable")]
    result = categorical(rows, "condition", "complete", "incomplete", "not_applicable")
    assert result["applicable"] == 3 and result["not_applicable"] == 1
    assert result["lower_bound"] == 1/3 and result["upper_bound"] == 2/3


def test_missing_or_duplicate_rating_is_rejected():
    row = {"review_id": "N", "support": "supported", "reason": "source", "source_lines": [1308]}
    fields = {"support": {"supported", "unsupported", "uncertain"}}
    with pytest.raises(ValueError, match="Incomplete"):
        validate_ratings([], {"N": {}}, fields, 1308, 1809)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_ratings([row, row], {"N": {}}, fields, 1308, 1809)
    validate_ratings([row], {"N": {}}, fields, 1308, 1809)
