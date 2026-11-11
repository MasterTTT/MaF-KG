"""Audit saved applicability pairs without modifying experimental responses."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_memory_ablation import digest, write_json


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_response(response: dict, payload: dict) -> list[str]:
    issues = []
    actions = response.get("node_actions", [])
    supplied = {row["id"] for row in payload["graph_state"]["nodes"]}
    refs = [row["ref_id"] for row in actions]
    if len(set(refs)) != len(refs):
        issues.append("duplicate_local_ref")
    allowed = supplied | set(refs)
    lines = set(payload["current_evidence"]["evidence_line_numbers"])
    latex_ids = {row["candidate_id"] for row in payload["current_evidence"]["source_latex_candidates"]}
    for row in actions:
        if row["action"] == "reuse" and row.get("existing_id") not in supplied:
            issues.append("reuse_outside_memory")
    for row in response.get("relations", []):
        if not {row["source_ref"], row["target_ref"]} <= allowed:
            issues.append("relation_unknown_endpoint")
    for row in response.get("expressions", []):
        if row["owner_ref"] not in set(refs):
            issues.append("expression_unknown_owner")
        if row["latex_candidate_id"] not in latex_ids:
            issues.append("unknown_latex_candidate")
    for row in [*actions, *response.get("relations", []), *response.get("expressions", [])]:
        evidence = row["evidence"]
        start, end = evidence["start_line"], evidence["end_line"]
        if start > end or start not in lines or end not in lines:
            issues.append("evidence_outside_current_region")
    return issues


def audit(directory: Path) -> dict:
    manifest = read(directory / "manifest.json")
    rows = []
    pairs = []
    for case in manifest["cases"]:
        folder = directory / case["name"]
        payloads = {arm: read(folder / f"{arm}_request.json") for arm in ("control", "source_context")}
        control, treatment = (copy.deepcopy(payloads[arm]) for arm in payloads)
        control.pop("source_applicability_context")
        excerpts = treatment.pop("source_applicability_context")
        if control != treatment:
            raise ValueError("Non-context intervention detected")
        source = folder / f"{('differential' if case['name'] == 'error_propagation' else 'fundamental')}.md"
        source_lines = source.read_text(encoding="utf-8").splitlines()
        for excerpt in excerpts:
            expected = "\n".join(f"L{i}: {source_lines[i - 1]}" for i in
                                 range(excerpt["start_line"], excerpt["end_line"] + 1))
            if excerpt["text"] != expected or excerpt["source_sha256"] != digest(source):
                raise ValueError("Supplementary provenance mismatch")
        pairs.append({"case": case["name"], "identical_noncontext_payload": True,
                      "exact_source_excerpts": True})
        for repeat in range(1, manifest["repeats"] + 1):
            for arm, payload in payloads.items():
                path = folder / f"repeat_{repeat}_{arm}.json"
                saved = read(path)
                response = saved["response"]
                rows.append({"case": case["name"], "repeat": repeat, "arm": arm,
                    "response_sha256": digest(path), "issues": audit_response(response, payload),
                    "actions": len(response["node_actions"]),
                    "relations": len(response.get("relations", [])),
                    "api_attempts": len(saved["requests"]),
                    "tokens": sum(row.get("usage", {}).get("total_tokens", 0) for row in saved["requests"]),
                    "latency_seconds": sum(row["latency_seconds"] for row in saved["requests"])})
    return {"scope": "request/provenance/schema-reference audit, not complete merge or semantic audit",
            "pairs": pairs, "responses": rows,
            "all_reference_checks_passed": all(not row["issues"] for row in rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    write_json(args.directory / "request_audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
