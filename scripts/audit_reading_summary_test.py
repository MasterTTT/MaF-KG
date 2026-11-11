"""Check summary intervention isolation and saved output references."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from audit_applicability_context_test import audit_response
from extraction_context_state import data_hash
from run_memory_ablation import digest, write_json
from run_reading_summary_test import ARMS, check_summary, summary_request


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(directory: Path) -> dict:
    manifest = read(directory / "manifest.json")
    rows, summaries, pair_checks = [], [], []
    for case in manifest["cases"]:
        folder = directory / case["name"]
        base = read(folder / "base_request.json")
        generated = read(folder / "generated_summary.json")
        if "reused_summary_sha256" in case and digest(folder / "generated_summary.json") != case["reused_summary_sha256"]:
            raise ValueError("Reused summary changed")
        note = generated["response"]
        summary_input = read(folder / "summary_request.json")
        if summary_input != summary_request(base) or generated["payload_hash"] != data_hash(summary_input):
            raise ValueError("Unexpected summary source input")
        check_summary(note, base)
        source_name = "differential" if case["name"] == "error_propagation" else "fundamental"
        source = folder / f"{source_name}.md"
        lines = source.read_text(encoding="utf-8").splitlines()
        for excerpt in base["source_applicability_context"]:
            text = "\n".join(f"L{i}: {lines[i - 1]}" for i in range(excerpt["start_line"], excerpt["end_line"] + 1))
            if excerpt["text"] != text or excerpt["source_sha256"] != digest(source):
                raise ValueError("Supplementary source changed")
        payloads = {arm: read(folder / f"{arm}_request.json") for arm in ARMS}
        plain, summarized = (copy.deepcopy(payloads[arm]) for arm in ARMS[1:])
        if summarized.pop("reading_state_summary") != note or plain.pop("reading_state_summary") is not None:
            raise ValueError("Incorrect summary injection")
        if plain != summarized or plain != base:
            raise ValueError("Primary contrast changed non-summary input")
        original = copy.deepcopy(payloads["original"])
        if original.pop("reading_state_summary") is not None or original["source_applicability_context"]:
            raise ValueError("Original arm received additional context")
        original["source_applicability_context"] = copy.deepcopy(base["source_applicability_context"])
        if original != base:
            raise ValueError("Original arm changed baseline")
        pair_checks.append({"case": case["name"], "summary_is_only_primary_difference": True,
                            "same_frozen_graph_memory": True, "original_source_preserved": True})
        summaries.append({"case": case["name"], "response_sha256": digest(folder / "generated_summary.json"),
            "generation_reused_from_prior_run": "reused_summary_sha256" in case,
            "tokens": sum(x.get("usage", {}).get("total_tokens", 0) for x in generated["requests"]),
            "api_attempts": len(generated["requests"]),
            "latency_seconds": sum(x["latency_seconds"] for x in generated["requests"])})
        for repeat in range(1, manifest["repeats"] + 1):
            for arm in manifest["arms"]:
                payload = payloads[arm]
                path = folder / f"repeat_{repeat}_{arm}.json"
                saved = read(path)
                prompt = (folder / "system_prompt.txt").read_text(encoding="utf-8")
                if saved["payload_hash"] != data_hash(payload) or saved["system_prompt_hash"] != data_hash(prompt):
                    raise ValueError("Executed payload or prompt differs from frozen input")
                response = saved["response"]
                rows.append({"case": case["name"], "repeat": repeat, "arm": arm,
                    "response_sha256": digest(path), "reference_issues": audit_response(response, payload),
                    "actions": len(response["node_actions"]), "relations": len(response.get("relations", [])),
                    "api_attempts": len(saved["requests"]),
                    "tokens": sum(x.get("usage", {}).get("total_tokens", 0) for x in saved["requests"]),
                    "latency_seconds": sum(x["latency_seconds"] for x in saved["requests"])})
    return {"scope": "Protocol and reference audit; no semantic certification or production merge",
            "pair_checks": pair_checks, "summaries": summaries, "responses": rows,
            "reference_checks_passed": all(not x["reference_issues"] for x in rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    write_json(args.directory / "request_audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
