import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from apply_ku_prerequisite_adjudication import apply_decisions


def fixture():
    reference = {"annotations": [
        {"id": "P4", "prerequisite_ku": "A", "target_ku": "B", "role": "uncertain", "provenance": "assistant_draft"},
        {"id": "OTHER", "prerequisite_ku": "B", "target_ku": "A", "role": "related_or_none", "provenance": "assistant_draft"}]}
    decisions = {"date": "2026-10-07", "decisions": [{"id": "P4", "prerequisite_ku": "A", "target_ku": "B",
        "role": "required", "directness": "direct", "reason": "approved", "chat_item": "A1"}]}
    catalog = {"knowledge_units": [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}]}
    return reference, decisions, catalog, {"items": []}


def test_only_explicit_pairs_get_human_provenance_and_original_is_unchanged():
    args = fixture()
    original = copy.deepcopy(args[0])
    result = apply_decisions(*args)
    assert args[0] == original
    assert result["annotations"][0]["provenance"] == "human_user_adjudicated"
    assert result["annotations"][0]["role"] == "required"
    assert result["annotations"][1]["provenance"] == "assistant_draft"


def test_duplicate_decision_is_rejected():
    args = fixture()
    args[1]["decisions"].append(copy.deepcopy(args[1]["decisions"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        apply_decisions(*args)


def test_unknown_endpoint_is_rejected():
    args = fixture()
    args[1]["decisions"][0]["target_ku"] = "unknown"
    with pytest.raises(ValueError, match="Unknown"):
        apply_decisions(*args)
