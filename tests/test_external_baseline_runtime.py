import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from external_baseline_runtime import GateReached, TextClient


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")

    def __iter__(self):
        choices = self.body.get("choices", [])
        event = {"model": self.body.get("model"), "usage": self.body.get("usage"),
                 "choices": [{"index": 0, "finish_reason": c["finish_reason"], "delta": c["message"]}
                             for c in choices]}
        if "echo" in self.body:
            event["echo"] = self.body["echo"]
        yield ("data: " + json.dumps(event) + "\n").encode("utf-8")
        yield b"data: [DONE]\n"


def client(tmp_path, **kwargs):
    return TextClient(tmp_path / "job", tmp_path / "ledger.json", "test", **kwargs)


def test_native_messages_and_no_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-not-a-real-key")
    captured = []

    def send(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response({"model": "deepseek-v4-flash", "choices": [
            {"finish_reason": "stop", "message": {"content": "native text"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12}})

    monkeypatch.setattr("urllib.request.urlopen", send)
    c = client(tmp_path, domain_instruction="domain")
    assert c([{"role": "user", "content": "source"}], "x") == "native text"
    assert captured[0]["messages"][0]["content"] == "domain"
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["stream"] is True
    assert "response_format" not in captured[0]
    assert "test-not-a-real-key" not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))


def test_truncation_accounted_without_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "not-a-real-key")
    sent = []

    def send(*args, **kwargs):
        sent.append(1)
        return Response({"choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

    monkeypatch.setattr("urllib.request.urlopen", send)
    with pytest.raises(RuntimeError):
        client(tmp_path)([{"role": "user", "content": "x"}])
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["requests"][0]["status"] == "reported_usage"
    assert len(sent) == 1


def test_call_gate_before_network(tmp_path, monkeypatch):
    with pytest.raises(GateReached):
        client(tmp_path, max_calls=0)([{"role": "user", "content": "x"}])
    assert not (tmp_path / "ledger.json").exists()


def test_credential_echo_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "private-key")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response({"echo": "private-key"}))
    with pytest.raises(RuntimeError):
        client(tmp_path)([{"role": "user", "content": "x"}])
    assert not list(tmp_path.rglob("responses/*.json"))


def test_concurrent_ledger_cannot_reserve(tmp_path):
    c = client(tmp_path)
    c.ledger.with_suffix(".lock").write_text("another active caller", encoding="utf-8")
    with pytest.raises(GateReached):
        c([{"role": "user", "content": "x"}])
    assert not c.ledger.exists()
