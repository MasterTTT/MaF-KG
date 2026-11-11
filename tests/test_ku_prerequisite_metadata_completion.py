import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from complete_ku_prerequisite_review_metadata import validate


def test_rejected_strength_none_is_preserved_not_repaired():
    row = {"index": 0, "accept": False, "strength": "none", "reason": "not required"}
    assert validate({"verdicts": [row]}, [(0, {})])[0] is row
    assert row["strength"] == "none"


def test_accept_still_requires_valid_strength():
    with pytest.raises(ValueError, match="strength"):
        validate({"verdicts": [{"index": 0, "accept": True, "strength": "none", "reason": "x"}]}, [(0, {})])


def test_duplicate_or_boolean_index_cannot_pass():
    row = {"index": 0, "accept": False, "strength": "none", "reason": "x"}
    with pytest.raises(ValueError, match="coverage"):
        validate({"verdicts": [row, row]}, [(0, {})])
    with pytest.raises(ValueError, match="coverage"):
        validate({"verdicts": [{**row, "index": False}]}, [(0, {})])
