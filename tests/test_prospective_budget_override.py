import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prospective_native_client import Budget


def test_explicit_budget_limit_preserves_accounting(tmp_path):
    path = tmp_path / "kimi.json"
    budget = Budget(path, "kimi-k2.6", usd=1.50)
    index = budget.reserve({"input": "example"}, 100, "request")
    budget.settle(index, None)
    restored = Budget(path, "kimi-k2.6", usd=1.50)
    assert restored.data["accounting_usd"] == budget.data["accounting_usd"]
    assert restored.data["requests"] == budget.data["requests"]
    with pytest.raises(ValueError, match="configuration changed"):
        Budget(path, "kimi-k2.6")
