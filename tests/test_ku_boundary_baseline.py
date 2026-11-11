import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_ku_boundary_baseline import heading_objectives


def card(i, child=None):
    return {"card_id": f"c{i}", "heading_path": ["section"] + ([child] if child else [])}


def test_intro_attaches_to_next_heading():
    result = heading_objectives("section", [card(1), card(2, "A"), card(3, "B")])
    assert [g["card_ids"] for g in result] == [["c1", "c2"], ["c3"]]


def test_no_child_heading_is_one_group():
    assert heading_objectives("section", [card(1), card(2)])[0]["card_ids"] == ["c1", "c2"]


def test_root_closing_card_attaches_to_previous_heading():
    result = heading_objectives("section", [card(1, "A"), card(2, "B"), card(3)])
    assert result[1]["card_ids"] == ["c2", "c3"]
