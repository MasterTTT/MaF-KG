import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from summarize_external_baseline_results import combine, failure_class


def test_failure_resources_counted_without_zero_quality(tmp_path):
    jobs = []
    for status in ("completed", "failed"):
        jobs.append({"method": "maf", "status": status, "actual_requests": 2,
            "usage_known_requests": 1, "elapsed_seconds": 10,
            "native_usage": {"prompt_tokens": 100, "completion_tokens": 50, "reasoning_tokens": 20},
            "issues": [], "section": "S", "repeat": 1, "directory": str(tmp_path)})
    result = combine({"jobs": jobs, "scope": "test"},
                     {"by_method": {"maf": {"reviewed": 1}}}, "deepseek-v4-flash")
    row = result["methods"]["maf"]
    assert row["execution_statuses"] == {"completed": 1, "failed": 1}
    assert row["usage_unknown_requests"] == 2
    assert row["reported_total_tokens"] == 300
    assert row["reported_native_usage"]["reasoning_tokens"] == 40
    assert row["quality"] == {"reviewed": 1}
    assert row["estimated_usd_for_known_usage_only"] == 0.00018


def test_resource_gate_classification_does_not_serialize_log(tmp_path):
    (tmp_path / "stdout.log").write_text("ValueError: Pre-request token budget gate reached", encoding="utf-8")
    assert failure_class(tmp_path) == "token_resource_gate"
