import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_external_baseline_retry import prepare


def test_retry_keeps_failed_parent_and_budget(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    manifest = {"session": "original", "budget_root": "shared-ledgers", "jobs": [
        {"status": "failed", "method": "itext2kg", "section": "S", "repeat": 1,
         "directory": "original-failed", "exit_code": 1},
        {"status": "completed", "method": "maf", "section": "S", "repeat": 1,
         "directory": "original-success"}]}
    path = parent / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original = path.read_bytes()
    retry = prepare(parent, tmp_path / "retry")
    assert path.read_bytes() == original
    assert retry["budget_root"] == "shared-ledgers"
    assert retry["session"] != "original"
    assert len(retry["jobs"]) == 1
    job = retry["jobs"][0]
    assert job["status"] == "pending"
    assert job["original_attempt"]["status"] == "failed"
    assert "exit_code" not in job
    with pytest.raises(FileExistsError):
        prepare(parent, tmp_path / "retry")
