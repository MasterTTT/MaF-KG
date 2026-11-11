import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assemble_new_targets_reviewed import resolve_reused_concept_centers
from freeze_new_target_reference import freeze
from run_new_target_diagnostics import passages, validate_enhanced


def units(kind="Concept", later_kind="Claim"):
    node = {"id": "shared", "name": "Primitive", "type": kind}
    return [
        {"id": "early", "name": "Definition", "chapter": "第一章",
         "cards": [{"start_line": 10}], "centers_on": [node], "contains": []},
        {"id": "later", "name": "Theorem", "chapter": "第五章",
         "cards": [{"start_line": 20}], "centers_on": [copy.deepcopy(node),
         {"id": "distinct", "name": "Conclusion", "type": later_kind}], "contains": []},
    ]


def test_center_supplement_preserves_members_and_input():
    original = units()
    before = copy.deepcopy(original)
    corrected, changes = resolve_reused_concept_centers(original)
    assert original == before
    assert len(changes) == 1
    for a, b in zip(original, corrected, strict=True):
        assert {n["id"] for n in a["centers_on"] + a["contains"]} == {
            n["id"] for n in b["centers_on"] + b["contains"]}
    assert [n["id"] for n in corrected[1]["centers_on"]] == ["distinct"]


@pytest.mark.parametrize("kind,later_kind", [("Method", "Claim"), ("Concept", "Concept")])
def test_center_supplement_rejects_unjustified_resolution(kind, later_kind):
    with pytest.raises(ValueError):
        resolve_reused_concept_centers(units(kind, later_kind))


def test_reference_cannot_be_revised_after_prediction(tmp_path):
    (tmp_path / "prediction_stage.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="after prediction"):
        freeze(tmp_path)
    assert not (tmp_path / "frozen_inputs01").exists()


def witness_fixture():
    task = {"source": {"id": "s", "members": [{"id": "n", "description": "Exact description"}]},
            "eligible_targets": [{"id": "t"}],
            "source_passages": [{"passage_id": "p", "text": "Exact source statement",
                                  "source": "book.md", "start_line": 1, "end_line": 2}]}
    row = {"source_ku": "s", "target_ku": "t", "witness_node_id": "n",
           "witness_quote": "source statement", "evidence_source": "source_passage",
           "passage_id": "p", "reason": "Uses source evidence"}
    return task, row, {"s": {"name": "Source"}, "t": {"name": "Target"}}


def test_exact_source_witness_accepted():
    task, row, catalog = witness_fixture()
    result = validate_enhanced({"proposals": [row]}, [task], catalog)
    assert result[0]["proposal_witness"]["passage_id"] == "p"


@pytest.mark.parametrize("field,value", [("witness_quote", "Paraphrased statement"),
                                       ("target_ku", "outside"), ("passage_id", "missing")])
def test_invalid_source_proposal_rejected(field, value):
    task, row, catalog = witness_fixture()
    row[field] = value
    with pytest.raises(ValueError):
        validate_enhanced({"proposals": [row]}, [task], catalog)


def test_proposal_cap_rejected():
    task, row, catalog = witness_fixture()
    rows = []
    task["eligible_targets"] = [{"id": f"t{i}"} for i in range(4)]
    for i in range(4):
        rows.append({**row, "target_ku": f"t{i}"})
        catalog[f"t{i}"] = {"name": f"Target{i}"}
    with pytest.raises(ValueError, match="cap"):
        validate_enhanced({"proposals": rows}, [task], catalog)


def test_source_passages_skip_examples_and_respect_budget(tmp_path):
    source = tmp_path / "book.md"
    source.write_text("Example\nActual statement\nMore content\n", encoding="utf-8")
    shared = {"source_path": str(source), "discourse_roles": []}
    u = {"cards": [{**shared, "card_id": "e", "start_line": 1, "end_line": 1,
                    "teaching_unit_kind": "example"},
                   {**shared, "card_id": "p", "start_line": 2, "end_line": 3,
                    "teaching_unit_kind": "exposition"}]}
    result = passages(u, limit=8)
    assert len(result) == 1
    assert result[0]["passage_id"] == "p"
    assert result[0]["text"] == "Actual s"
    assert result[0]["truncated"] is True
