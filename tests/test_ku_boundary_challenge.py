import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_ku_boundary_challenge as challenge


def fixture(tmp_path, combined=False, duplicate=False, separate=True):
    groups = [{"name": "A", "member_ids": ["a", "b"]},
              {"name": "B", "member_ids": ["c", "d"]}]
    reference = {"jobs": {"job": {"groups": groups, "l1_missing": [], "separate_groups": separate}}}
    challenge.write(tmp_path / "reference.json", reference)
    units = [{"id": "u1", "name": "A", "description": "First goal",
              "centers_on": [{"id": "a", "name": "a"}],
              "contains": [{"id": "b", "name": "b"}]}]
    second = {"id": "u2", "name": "B", "description": "Second goal",
              "centers_on": [{"id": "c", "name": "c"}], "contains": [{"id": "d", "name": "d"}]}
    if combined:
        units[0]["contains"] += second["centers_on"] + second["contains"]
    else:
        units.append(second)
    if duplicate:
        units[-1]["contains"].append({"id": "a", "name": "a"})
    output = tmp_path / "output.json"
    challenge.write(output, {"knowledge_units": units})
    challenge.write(tmp_path / "manifest.json", {
        "bindings": [{"path": str(tmp_path / "reference.json"), "sha256": challenge.sha(tmp_path / "reference.json")}],
        "jobs": [{"id": "job", "status": "completed", "output": str(output),
                  "sha256": challenge.sha(output), "node_count": 4}]})


def test_complete_coverage_does_not_hide_overmerge(tmp_path):
    fixture(tmp_path, combined=True)
    challenge.score(tmp_path)
    result = challenge.read(tmp_path / "constraint_scores.json")["jobs"]["job"]
    assert result["member_coverage"] == {"hits": 4, "denominator": 4}
    assert result["must_link"]["hits"] == 2
    assert result["cannot_link"] == {"hits": 0, "denominator": 1,
                                      "pairs": [{"a": "a", "b": "c", "pass": False}]}


@pytest.mark.parametrize("combined", [False, True])
def test_alternative_valid_granularities_not_penalized(tmp_path, combined):
    fixture(tmp_path, combined=combined, separate=False)
    challenge.score(tmp_path)
    result = challenge.read(tmp_path / "constraint_scores.json")["jobs"]["job"]
    assert result["must_link"]["hits"] == 2
    assert result["cannot_link"]["denominator"] == 0


def test_changed_reference_rejected(tmp_path):
    fixture(tmp_path)
    challenge.write(tmp_path / "reference.json", {"changed": True})
    with pytest.raises(ValueError, match="changed"):
        challenge.score(tmp_path)


def test_duplicate_membership_rejected(tmp_path):
    fixture(tmp_path, duplicate=True)
    with pytest.raises(ValueError, match="Duplicate"):
        challenge.score(tmp_path)


def test_failed_job_not_given_success_scores(tmp_path):
    challenge.write(tmp_path / "reference.json", {"jobs": {}})
    challenge.write(tmp_path / "manifest.json", {"bindings": [], "jobs": [{"id": "job", "status": "failed"}]})
    challenge.score(tmp_path)
    result = challenge.read(tmp_path / "constraint_scores.json")["jobs"]["job"]
    assert result["status"] == "failed"
    assert "must_link" not in result
