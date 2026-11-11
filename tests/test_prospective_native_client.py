import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prospective_native_client import Budget, normalize, payload


def test_native_parameters():
    assert payload("deepseek-v4-flash", "s", "u", 64)[1]["reasoning_effort"] == "low"
    assert "thinking" not in payload("glm-5.3-flash", "s", "u", 64)[1]
    route, body = payload("gpt-6-luna", "s", "u", 64)
    assert route == "responses" and body["store"] is False
    assert body["instructions"] == "s" and body["input"] == "u"
    for model in ("mimo-v2.6-flash", "kimi-k2.6"):
        assert payload(model, "s", "u", 64)[0] == "chat/completions"
        assert "thinking" not in payload(model, "s", "u", 64)[1]


def test_responses_normalization():
    native = {"status":"completed", "output":[{"type":"message", "role":"assistant",
               "content":[{"type":"output_text", "text":"{}"}]}],
               "usage":{"input_tokens":10,"output_tokens":4,"total_tokens":14}}
    result = normalize("gpt-6-luna", native)
    assert result["choices"][0]["message"]["content"] == "{}"
    assert result["usage"]["prompt_tokens"] == 10
    native["status"] = "incomplete"
    with pytest.raises(ValueError, match="not completed"):
        normalize("gpt-6-luna", native)


def test_budget_blocks_before_request(tmp_path):
    budget = Budget(tmp_path / "b.json", "deepseek-v4-flash", usd=0.00001)
    with pytest.raises(ValueError, match="budget gate"):
        budget.reserve({"input":"hello"}, 100, "a")
    assert not budget.path.exists()


def test_unknown_usage_retains_reservation(tmp_path):
    path = tmp_path / "b.json"
    budget = Budget(path, "gpt-6-luna")
    index = budget.reserve({"input":"hello"}, 100, "a")
    bound = budget.data["accounting_usd"]
    with pytest.raises(ValueError, match="Unsettled"):
        Budget(path, "gpt-6-luna")
    budget.settle(index, None)
    assert Budget(path, "gpt-6-luna").data["accounting_usd"] == bound


def test_success_accounting_and_double_settlement(tmp_path):
    budget = Budget(tmp_path / "b.json", "glm-5.3-flash")
    index = budget.reserve({"input":"hello"}, 100, "a")
    budget.settle(index, {"prompt_tokens":20,"completion_tokens":10})
    assert budget.data["accounting_tokens"] == 30
    with pytest.raises(ValueError, match="already settled"):
        budget.settle(index, None)


def test_token_only_budget_extension_preserves_cumulative_accounting(tmp_path):
    path = tmp_path / "glm.json"
    original = Budget(path, "glm-5.3-flash", usd=0.50, tokens=1_000_000)
    original.data["accounting_usd"] = 0.25
    original.data["accounting_tokens"] = 950_000
    original.data["requests"] = [{"label": "prior", "status": "reported_usage"}]
    path.write_text(json.dumps(original.data), encoding="utf-8")

    with pytest.raises(ValueError, match="token budget gate"):
        original.reserve({"input": "example"}, 100_000, "blocked")

    approved = json.loads(path.read_text(encoding="utf-8"))
    approved["token_limit"] = 1_500_000
    path.write_text(json.dumps(approved), encoding="utf-8")
    extended = Budget(path, "glm-5.3-flash", usd=0.50, tokens=1_500_000)
    prior_accounting = (extended.data["accounting_usd"], extended.data["accounting_tokens"],
                        list(extended.data["requests"]))
    index = extended.reserve({"input": "example"}, 100_000, "approved")

    assert extended.data["usd_limit"] == 0.50
    assert extended.data["token_limit"] == 1_500_000
    reservation = extended.data["requests"][-1]
    assert extended.data["accounting_usd"] == prior_accounting[0] + reservation["bound_usd"]
    assert extended.data["accounting_tokens"] == prior_accounting[1] + reservation["bound_tokens"]
    assert extended.data["requests"][:-1] == prior_accounting[2]
    extended.settle(index, None)
