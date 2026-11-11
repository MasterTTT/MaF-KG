import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from finalize_assisted_prerequisite_review import validate_decisions


def fixture():
    packet = {"items": [
        {"item_id": "S1", "dependent": {"id": "B"}, "prerequisite": {"id": "A"}},
        {"item_id": "S2", "dependent": {"id": "C"}, "prerequisite": {"id": "A"}}]}
    decisions = {"decisions": [{"item_id": "S1", "role": "proof_support"},
                                {"item_id": "S2", "role": "required", "directness": "direct"}]}
    protected = {"annotations": [{"target_ku": "B", "prerequisite_ku": "A", "role": "proof_support"}]}
    return packet, decisions, protected


def test_complete_review_preserves_user_decisions():
    args = fixture()
    before = copy.deepcopy(args)
    items, locked = validate_decisions(*args)
    assert len(items) == 2 and len(locked) == 1
    assert args == before


def test_user_decision_cannot_be_overridden():
    args = fixture()
    args[1]["decisions"][0].update(role="required", directness="direct")
    with pytest.raises(ValueError, match="user decision"):
        validate_decisions(*args)


def test_missing_or_duplicate_review_is_rejected():
    args = fixture()
    args[1]["decisions"].pop()
    with pytest.raises(ValueError, match="every packet"):
        validate_decisions(*args)
    args = fixture()
    args[1]["decisions"].append(copy.deepcopy(args[1]["decisions"][0]))
    with pytest.raises(ValueError, match="every packet"):
        validate_decisions(*args)


def test_invalid_role_and_directness_are_rejected():
    args = fixture()
    args[1]["decisions"][1]["role"] = "maybe"
    with pytest.raises(ValueError, match="Invalid"):
        validate_decisions(*args)
    args = fixture()
    args[1]["decisions"][1].pop("directness")
    with pytest.raises(ValueError, match="directness"):
        validate_decisions(*args)
