"""Join audited resource use and semantic reviews without treating failures as zeros."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from prospective_native_client import RATES, write


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def failure_class(directory):
    log = Path(directory) / "stdout.log"
    if not log.exists():
        return "not_executed_or_log_absent"
    text = log.read_text(encoding="utf-8")
    for marker, label in (
        ("Pre-request token budget gate", "token_resource_gate"),
        ("Pre-request USD budget gate", "usd_resource_gate"),
        ("request/time gate", "task_resource_gate"),
        ("ChatCallLimitReached", "native_adapter_call_gate"),
        ("call limit reached", "native_adapter_call_gate"),
        ("RemoteDisconnected", "transport_disconnect"),
        ("Timeout", "timeout"),
        ("Non-complete native response", "incomplete_response"),
        ("ValidationError", "native_contract_error"),
    ):
        if marker in text:
            return label
    return "other_failure_see_preserved_log"


def combine(audit, scores, model):
    incoming_rate, outgoing_rate = RATES[model]
    methods = sorted({j["method"] for j in audit["jobs"]})
    result = {}
    for method in methods:
        jobs = [j for j in audit["jobs"] if j["method"] == method]
        requests = sum(j["actual_requests"] for j in jobs)
        known = sum(j["usage_known_requests"] for j in jobs)
        usage = {key: sum(j["native_usage"][key] for j in jobs)
                 for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens")}
        costs = (usage["prompt_tokens"] * incoming_rate +
                 usage["completion_tokens"] * outgoing_rate) / 1_000_000
        elapsed = sum(j.get("elapsed_seconds", 0) for j in jobs)
        result[method] = {
            "planned_outputs": len(jobs),
            "execution_statuses": dict(Counter(j["status"] for j in jobs)),
            "actual_requests_including_failed_jobs": requests,
            "usage_known_requests": known,
            "usage_unknown_requests": requests - known,
            "reported_native_usage": usage,
            "reported_total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
            "estimated_usd_for_known_usage_only": costs,
            "summed_task_wall_seconds": elapsed,
            "payload_or_hash_issues": [issue for j in jobs for issue in j["issues"]],
            "failure_details": [{"section": j["section"], "repeat": j["repeat"],
                                 "status": j["status"], "directory": j["directory"],
                                 "class": failure_class(j["directory"])}
                                for j in jobs if j["status"] != "completed"],
            "quality": scores["by_method"].get(method),
        }
    return {
        "methods": result,
        "scope": audit["scope"],
        "quality_provenance": "AI-assisted development source review; not independent expert Gold",
        "independent_unit_note": "Three exposed textbook sections, two technical repeats; no significance test",
        "cost_note": "Local conservative peak-rate estimate without cache discounts; not Go billing or remaining quota",
        "missing_note": "Failures/pending outputs have no quality score, never a zero or an imputed success",
        "representation_note": "Native support ratios and graph counts differ in granularity; no cross-ontology total F1",
    }


def summarize(run, audit_path, scores_path, output):
    manifest = read(run / "manifest.json")
    result = combine(read(audit_path), read(scores_path), manifest["common"]["model"])
    result["manifest"] = str(run / "manifest.json")
    result["method_ledgers_including_pilots"] = {}
    for method in result["methods"]:
        ledger = read(Path(manifest["budget_root"]) / f"{method}.json")
        statuses = Counter(r["status"] for r in ledger["requests"])
        result["method_ledgers_including_pilots"][method] = {
            "usd_limit": ledger["usd_limit"], "token_limit": ledger["token_limit"],
            "accounting_usd": ledger["accounting_usd"],
            "accounting_tokens": ledger["accounting_tokens"],
            "request_statuses": dict(statuses),
            "unsettled": statuses["reserved"],
        }
    write(output, result)
    print(f"Summarized {len(result['methods'])} methods; no inferred significance or quality imputation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("audit", type=Path)
    parser.add_argument("scores", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summarize(args.run.resolve(), args.audit.resolve(), args.scores.resolve(), args.output.resolve())
