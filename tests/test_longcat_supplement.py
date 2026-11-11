import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prospective_native_client import Budget, RATES, payload


def test_longcat_native_payload():
    route, body = payload("longcat-2.0", "system", "user", 24000)
    assert route == "chat/completions"
    assert body["model"] == "longcat-2.0"
    assert "thinking" not in body and "reasoning_effort" not in body
    assert "temperature" not in body
    assert RATES["longcat-2.0"] == (0.30, 1.20)


def test_longcat_budget_keeps_default_limit(tmp_path):
    budget = Budget(tmp_path / "longcat.json", "longcat-2.0")
    assert budget.usd == 0.50 and budget.tokens == 1000000
