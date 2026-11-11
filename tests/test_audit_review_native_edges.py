import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_review_native_edges import contradictions


def fixture():
    packet = {"items": [
        {"item_id": "N1", "kind": "entity", "native": {"id": "A"}},
        {"item_id": "N2", "kind": "entity", "native": {"id": "B"}},
        {"item_id": "E1", "kind": "assertion", "native": {
            "type": "special_case_of", "source_id": "A", "target_id": "B"}}],
        "reference": {"relations": [{"id": "R", "start": "X", "end": "Y", "type": "special_case_of"}]}}
    review = {"node_coverage": [{"reference_id": "X", "item_ids": ["N1"]},
                               {"reference_id": "Y", "item_ids": ["N2"]}],
              "relation_coverage": [{"reference_id": "R", "status": "partial", "reason": "No edge"}]}
    return packet, review


def test_existing_edge_cannot_be_silently_described_as_absent():
    result = contradictions(*fixture())
    assert result[0]["exact_native_edges"] == ["E1"]


def test_direction_and_type_are_not_interchangeable():
    packet, review = fixture()
    packet["items"][2]["native"]["source_id"] = "B"
    assert contradictions(packet, review) == []
    packet, review = fixture()
    packet["items"][2]["native"]["type"] = "defined_in_terms_of"
    assert contradictions(packet, review) == []


def test_not_a_second_grader_of_full_or_missing_output():
    packet, review = fixture()
    review["relation_coverage"][0]["status"] = "full"
    assert contradictions(packet, review) == []
    packet, review = fixture()
    packet["items"] = copy.deepcopy(packet["items"][:2])
    assert contradictions(packet, review) == []
