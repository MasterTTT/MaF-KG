import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_ku_prerequisite_phase as phase


TEST_DEADLINE = datetime.max.replace(tzinfo=timezone.utc).isoformat()


def test_native_missing_key_does_not_reserve(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    client = phase.Native(tmp_path, {"usd_limit": .50, "token_limit": 400000, "deadline": TEST_DEADLINE})
    with pytest.raises(RuntimeError, match="missing"):
        client.call("s", "u", phase.MODEL)
    assert client.budget.data["requests"] == []


def fake_response(content, finish="stop"):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"choices": [{"finish_reason": finish, "message": {"content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10}}).encode("utf-8")
    return Response()


@pytest.mark.parametrize("content,finish,error", [
    ('```json\n{}\n```', 'stop', json.JSONDecodeError),
    ('{}', 'length', ValueError),
    ('[]', 'stop', ValueError),
])
def test_no_json_repairs_and_account_before_validation(tmp_path, monkeypatch, content, finish, error):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-local-secret")
    monkeypatch.setattr(phase.urllib.request, "urlopen", lambda *a, **k: fake_response(content, finish))
    manifest = {"usd_limit": .50, "token_limit": 400000, "max_tokens": 100,
                "session": "test", "timeout_seconds": 1, "deadline": TEST_DEADLINE}
    client = phase.Native(tmp_path, manifest)
    with pytest.raises(error):
        client.call("system", "user", phase.MODEL)
    assert client.budget.data["requests"][0]["status"] == "reported_usage"
    assert len(client.budget.data["requests"]) == 1
    request = (tmp_path / "native_requests/request_001.json").read_text(encoding="utf-8")
    assert "test-local-secret" not in request
    assert '"reasoning_effort": "low"' in request


def test_expired_manifest_deadline_stops_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-local-secret")
    client = phase.Native(tmp_path, {"usd_limit": .50, "token_limit": 400000,
                                    "deadline": "2000-01-01T00:00:00+00:00"})
    with pytest.raises(RuntimeError, match="deadline"):
        client.call("s", "u", phase.MODEL)
    assert client.budget.data["requests"] == []


def test_reference_required_before_candidates(tmp_path):
    phase.write(tmp_path / "manifest.json", {"bindings": [],
        "stages": {"organize": "completed", "candidates": "pending"}})
    with pytest.raises(ValueError, match="reference"):
        phase.candidates(tmp_path)


def test_hash_gate(tmp_path):
    p = tmp_path / "input.json"
    p.write_text("{}", encoding="utf-8")
    frozen = {"bindings": [{"path": str(p), "sha256": phase.sha(p)}]}
    phase.audit(frozen)
    p.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        phase.audit(frozen)


def make_assembly_fixture(tmp_path, duplicate_center=False):
    jobs = []
    node = {"id": "n", "name": "shared method", "type": "Method"}
    relation = {"source_id": "n", "target_id": "n", "type": "uses"}
    for i in range(2):
        unit = {"id": f"u{i}", "name": f"Unit{i}", "centers_on": [], "contains": []}
        unit["centers_on" if i == 0 or duplicate_center else "contains"] = [node]
        up, pp, gp = (tmp_path / f"{key}{i}.json" for key in ("unit", "plan", "graph"))
        phase.write(up, {"knowledge_units": [unit]})
        phase.write(pp, {"nodes": [node]})
        phase.write(gp, {"relations": [{**relation, "evidence": [i]}]})
        jobs.append({"id": str(i), "status": "completed", "output": str(up),
                     "sha256": phase.sha(up), "plan": str(pp), "graph": str(gp)})
    phase.write(tmp_path / "manifest.json", {"bindings": [], "jobs": jobs,
        "scope": "fixture", "stages": {"organize": "running"}})


def test_shared_contains_and_relation_metadata_are_preserved(tmp_path):
    make_assembly_fixture(tmp_path)
    phase.merge(tmp_path)
    assert len(phase.read(tmp_path / "graph.json")["nodes"]) == 1
    assert len(phase.read(tmp_path / "ku.json")["knowledge_units"]) == 2
    assert len(phase.read(tmp_path / "relation_assembly_audit.json")["repeated_signatures"][0]) == 2
    phase.audit(phase.read(tmp_path / "manifest.json"))


def test_duplicate_centers_still_fail(tmp_path):
    make_assembly_fixture(tmp_path, duplicate_center=True)
    with pytest.raises(ValueError, match="multiple owners"):
        phase.merge(tmp_path)
