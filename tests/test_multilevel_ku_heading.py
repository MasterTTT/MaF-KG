import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_ku_multilevel_heading import multilevel_objectives


def cards(paths):
    return [{"card_id": f"c{i}", "heading_path": ["Section"] + p} for i, p in enumerate(paths)]


def test_intro_stays_in_own_branch():
    result = multilevel_objectives("Section", cards([
        [], ["一、A"], ["一、A", "1. A1"], ["一、A", "2. A2"],
        ["二、B"], ["二、B", "1. B1"]]))
    assert [r["card_ids"] for r in result] == [["c0", "c1", "c2"], ["c3"], ["c4", "c5"]]


def test_identically_named_children_are_not_merged_across_parents():
    result = multilevel_objectives("Section", cards([["一、A", "1. Definition"], ["二、B", "1. Definition"]]))
    assert len(result) == 2


def test_theorem_heading_is_not_new_teaching_boundary():
    result = multilevel_objectives("Section", cards([[], ["定理1"], ["证明"], ["例1"]]))
    assert len(result) == 1
    assert result[0]["card_ids"] == ["c0", "c1", "c2", "c3"]


def test_parent_without_children_is_terminal():
    result = multilevel_objectives("Section", cards([["一、A"], ["二、B"], ["二、B", "1. B1"]]))
    assert len(result) == 2


def test_trailing_parent_content_attaches_to_own_previous_child():
    result = multilevel_objectives("Section", cards([["一、A", "1. A1"], ["一、A"], ["二、B", "1. B1"]]))
    assert result[0]["card_ids"] == ["c0", "c1"]


def test_empty_cards_rejected():
    with pytest.raises(ValueError, match="No Cards"):
        multilevel_objectives("Section", [])
