import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_finite_prerequisite_pilot import make_inputs


def test_only_neutral_candidate_fields_are_sent():
    pool = {"evidence": [{"id": "E", "source": "ch", "line_start": 1, "line_end": 1, "quote": "source"}],
            "candidates": [{"id": "P", "target_anchor": "I", "prerequisite_anchor": "A",
                            "evidence_ids": ["E"], "label": "positive", "reason": "hidden answer"},
                           {"id": "C", "target_anchor": "O2", "prerequisite_anchor": None, "evidence_ids": ["E"]}]}
    items, records = make_inputs(pool)
    assert len(items) == len(records) == 1
    assert set(records[0]) == {"index", "source", "target", "source_excerpts"}
    assert "label" not in str(records) and "hidden answer" not in str(records)
    assert "not a prerequisite" not in str(records)


def test_explicit_direction_preserves_reverse_candidate():
    pool = {"evidence": [], "candidates": [{"id": "R", "target_anchor": "D",
            "prerequisite_anchor": "T", "evidence_ids": []}]}
    _, records = make_inputs(pool, explicit_direction=True)
    assert records[0]["candidate_edge"] == "高阶导数与逐次求导 depends_on 泰勒公式与余项"
    assert records[0]["source"]["name"] == "高阶导数与逐次求导"
    assert records[0]["target"]["name"] == "泰勒公式与余项"
