"""Regression tests for the repaired, actually used review view and validators."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from www_supplement_closeout import make_packet, strict_from_bindings, validate_review
from www_supplement_e1_worksheets import graph_shape, native_items


def typed_graph():
    return {"nodes": [{"id": "a", "name": "result", "type": "Claim", "description": "x" * 900,
                        "logical_form": {"premises": ["p"], "conclusion": ["q"]},
                        "evidence": [{"text": "source quotation must not be output"}]},
                       {"id": "b", "name": "object", "type": "Concept", "description": "definition"}],
            "relations": [{"id": "e", "source_id": "a", "target_id": "b", "type": "involves"}],
            "math_expressions": [{"id": "x", "owner_id": "a", "role": "claim_representation",
                                  "latex": "p \\Rightarrow q", "evidence": [{"text": "citation"}]}]}


def reference():
    return {"nodes": [{"id": "n", "type": "Claim", "status": "M", "name": "result",
                       "conditions": ["p"], "source_evidence": [1, 2]},
                      {"id": "m", "type": "Concept", "status": "O", "name": "object",
                       "source_evidence": [2, 2]}],
            "relations": [{"id": "r", "type": "involves", "start": "n", "end": "m",
                           "source_evidence": [1, 2]}]}


def packet():
    return make_packet("C01", typed_graph(), reference(), {1: "source p", 2: "source q"})


def answer():
    return {"packet_id": "C01", "item_rows": [
        {"item_id": "n", "coverage": "full", "conditions_complete": True,
         "item_ids": ["a"], "edge_ids": [], "expression_ids": ["x"], "reason": "explicit"},
        {"item_id": "m", "coverage": "full", "conditions_complete": None,
         "item_ids": ["b"], "edge_ids": [], "expression_ids": [], "reason": "defined"}],
        "relation_rows": [{"relation_id": "r", "semantic": "full", "start_ids": ["a"],
                           "end_ids": ["b"], "edge_ids": ["e"], "reason": "edge"}]}


def test_actual_packet_preserves_logic_owned_formula_and_full_description():
    p = packet()
    n = p["native_output"]["items"][0]
    assert len(n["description"]) == 900
    assert n["logical_form"]["premises"] == ["p"]
    assert n["owned_expressions"][0]["latex"] == "p \\Rightarrow q"
    assert "evidence" not in n and "evidence" not in n["owned_expressions"][0]
    assert p["native_output"]["edges"][0]["id"] == "e"


def test_every_relation_has_source_context():
    p = packet()
    assert p["reference_relations"][0]["source"][0]["text"] == "1: source p\n2: source q"


def test_flat_and_nested_triples_are_both_readable():
    flat = [["A", "means", "B"], ["a", "contrasts", "A"]]
    assert graph_shape(flat) == "triple_list"
    assert graph_shape([flat]) == "nested_triple_list"
    for graph in (flat, [flat]):
        entities, edges = native_items(graph)
        assert {n["id"] for n in entities} == {"A", "a", "B"}
        assert len(edges) == 2


def test_entity_relationships_keep_effective_properties_but_not_embeddings():
    g = {"entities": [{"name": "A", "properties": {"embeddings": [1, 2], "formula": "f(x)"}},
                       {"name": "B", "properties": {}}],
         "relationships": [{"startNode": {"name": "A"}, "endNode": {"name": "B"},
                            "name": "free predicate", "description": "assertion"}]}
    entities, edges = native_items(g)
    assert entities[0]["properties"] == {"formula": "f(x)"}
    assert edges[0]["source"] == "A" and edges[0]["target"] == "B"
    assert edges[0]["description"] == "assertion"


def test_complete_answer_passes():
    assert validate_review(packet(), answer())


@pytest.mark.parametrize("change", ["unknown_id", "null_condition", "full_false", "missing_true", "duplicate"])
def test_bad_review_is_not_silently_repaired(change):
    a = answer()
    if change == "unknown_id":
        a["item_rows"][0]["item_ids"] = ["invented"]
    elif change == "null_condition":
        a["item_rows"][0]["conditions_complete"] = None
    elif change == "full_false":
        a["item_rows"][0]["conditions_complete"] = False
    elif change == "missing_true":
        a["item_rows"][0]["coverage"] = "missing"
    else:
        a["item_rows"].append(copy.deepcopy(a["item_rows"][0]))
    with pytest.raises(ValueError):
        validate_review(packet(), a)


def test_exact_reference_matching_is_not_synonym_mapping():
    row = answer()["relation_rows"][0]
    edges = packet()["native_output"]["edges"]
    assert strict_from_bindings({"type": "involves"}, row, edges) == "conforms"
    assert strict_from_bindings({"type": "involves"}, row, [{**edges[0], "type": "mentions"}]) == "type_mismatch"
    assert strict_from_bindings({"type": "involves"}, row,
                                [{**edges[0], "source": "b", "target": "a"}]) == "direction_mismatch"


def test_no_connected_edge_is_not_comparable():
    assert strict_from_bindings({"type": "involves"}, answer()["relation_rows"][0], []) == "not_comparable"
