from __future__ import annotations

import pytest

from scripts.summarize_prospective_quality_reviews import score_graph


def _fixture():
    reference = {
        "nodes": [
            {"id": "N1", "section": "s1", "status": "M", "type": "Concept", "name": "A"},
            {"id": "N2", "section": "s1", "status": "M", "type": "Method", "name": "B"},
            {"id": "N3", "section": "s1", "status": "M", "type": "Concept", "name": "C"},
        ],
        "relations_provisional": [
            {"id": "R1", "section": "s1", "start": "N2", "type": "uses", "end": "N1"},
            {"id": "R2", "section": "s1", "start": "N1", "type": "related_to", "end": "N3"},
        ],
    }
    graph = {
        "nodes": [
            {"id": "a", "type": "Concept", "name": "A"},
            {"id": "b", "type": "Method", "name": "B"},
            {"id": "c", "type": "Claim", "name": "C"},
        ],
        "relations": [
            {"id": "e1", "source_id": "b", "type": "uses", "target_id": "a"},
            {"id": "e2", "source_id": "c", "type": "related_to", "target_id": "a"},
        ],
    }
    identities = [
        {"item_id": "N1", "identity_id": "a", "identity_status": "strict_type_match"},
        {"item_id": "N2", "identity_id": "b", "identity_status": "matched"},
        {"item_id": "N3", "identity_id": "c", "identity_status": "type_mismatch"},
    ]
    relations = [
        {"reference_id": "R1", "source_item": "N2", "type": "uses", "target_item": "N1", "exact_match": True,
         "strict_endpoint_ids": {"source": "b", "target": "a"}, "predicted_relation": {"relation_id": "e1", "source_id": "b", "type": "uses", "target_id": "a"}},
        {"reference_id": "R2", "source_item": "N1", "type": "related_to", "target_item": "N3", "exact_match": True,
         "strict_endpoint_ids": {"source": "a", "target": "c"}, "predicted_relation": {"relation_id": "e2", "source_id": "c", "type": "related_to", "target_id": "a"}},
    ]
    return reference, graph, identities, relations


def test_recomputes_typed_identity_and_directed_relation_hits_from_graph():
    reference, graph, identities, relations = _fixture()

    result = score_graph(reference, graph, "s1", identities, relations)

    assert (result["mandatory_typed_identity_hits"], result["mandatory_typed_identity_total"]) == (2, 3)
    assert (result["exact_finite_relation_hits"], result["exact_finite_relation_total"]) == (1, 2)
    assert [row["exact_graph_hit"] for row in result["relation_results"]] == [True, False]
    assert len(result["relation_discrepancies"]) == 1


def test_rejects_non_one_to_one_strict_identity_mapping():
    reference, graph, identities, relations = _fixture()
    graph["nodes"].append({"id": "a2", "type": "Concept", "name": "A2"})
    identities[2] = {"item_id": "N3", "identity_id": "a", "identity_status": "strict_type_match"}

    with pytest.raises(ValueError, match="one-to-one"):
        score_graph(reference, graph, "s1", identities, relations)


def test_rejects_relation_alignment_endpoint_drift():
    reference, graph, identities, relations = _fixture()
    relations[0]["strict_endpoint_ids"]["source"] = "a"

    with pytest.raises(ValueError, match="strict endpoints"):
        score_graph(reference, graph, "s1", identities, relations)
