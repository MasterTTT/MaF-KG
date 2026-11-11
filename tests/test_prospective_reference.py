import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("prospective_audit", ROOT / "scripts/audit_prospective_reference.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def reference():
    return json.loads(audit.REFERENCE.read_text(encoding="utf-8"))


def test_reference_schema():
    audit.validate(reference())


def test_claim_special_case_rejected():
    data = reference()
    data["relations_provisional"][0]["type"] = "special_case_of"
    with pytest.raises(ValueError, match="endpoints"):
        audit.validate(data)


def test_duplicate_ownership_rejected():
    data = copy.deepcopy(reference())
    data["ku_partitions"]["ch3_s3"]["allowed"][1][1]["support_members"].append("T-N1")
    with pytest.raises(ValueError, match="assign each required node once"):
        audit.validate(data)


def test_reachability_does_not_treat_blank_as_missing():
    assert audit.reachability(1, 3, ["a", "\n", "b"], {1, 3})["status"] == "full"
    assert audit.reachability(1, 3, ["a", "\n", "b"], {1}) == {
        "status": "partial", "missing_nonempty_source_lines": [3]}
