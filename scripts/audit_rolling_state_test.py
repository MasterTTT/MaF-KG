"""Audit recursive source-state lineage and paired frozen-history requests."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from audit_applicability_context_test import audit_response
from extraction_context_state import data_hash
from run_memory_ablation import digest, write_json
from run_rolling_state_test import ARMS, check_state


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(directory: Path) -> dict:
    manifest = read(directory / "manifest.json")
    chunks = read(directory / "chunks.json")
    previous = None
    states, responses = [], []
    for index, chunk in enumerate(chunks):
        chunk_id = chunk["id"]
        if index:
            original = read(directory / "snapshot/requests" / f"{chunk_id}.json")["sent_payload"]
            for arm in ARMS:
                payload = read(directory / "paired_requests" / f"{chunk_id}_{arm}.json")
                stripped = copy.deepcopy(payload)
                injected = stripped.pop("rolling_reading_state")
                if stripped != original or injected != (previous if arm == "rolling_state" else None):
                    raise ValueError("Extraction state or frozen memory changed")
                path = directory / arm / f"{chunk_id}.json"
                saved = read(path)
                prompt = (directory / "snapshot/extraction_system_prompt.txt").read_text(encoding="utf-8")
                if saved["payload_hash"] != data_hash(payload) or saved["prompt_hash"] != data_hash(prompt):
                    raise ValueError("Executed request differs from saved input")
                response = saved["response"]
                responses.append({"chunk_id": chunk_id, "arm": arm, "response_sha256": digest(path),
                    "reference_issues": audit_response(response, payload),
                    "node_actions": len(response["node_actions"]), "relations": len(response.get("relations", [])),
                    "tokens": sum(x.get("usage", {}).get("total_tokens", 0) for x in saved["requests"]),
                    "api_attempts": len(saved["requests"]),
                    "latency_seconds": sum(x["latency_seconds"] for x in saved["requests"])})
        if index == len(chunks) - 1:
            break
        request_path = directory / "states" / f"{chunk_id}_request.json"
        request = read(request_path)
        commit = read(directory / "snapshot/commits" / f"{chunk_id}.json")
        expected_source = {key: chunk[key] for key in ("id", "start_line", "end_line", "text", "heading_path")}
        changed_ids = {x["node_id"] for x in commit["events"] if "node_id" in x}
        expected_delta = {"events": commit["events"],
                          "changed_nodes": {node_id: commit["after"]["nodes"][node_id] for node_id in sorted(changed_ids)},
                          "total_committed_nodes": len(commit["after"]["nodes"]),
                          "total_committed_relations": len(commit["after"]["relations"])}
        if request["previous_state"] != previous or request["just_processed_source"] != expected_source:
            raise ValueError("Recursive state input breaks chronological source lineage")
        if request["committed_graph_delta"] != expected_delta:
            raise ValueError("State uses uncommitted graph changes")
        if request["history_scope"]["processed_chunk_ids"] != [c["id"] for c in chunks[:index + 1]]:
            raise ValueError("Future chunk leaked through history scope")
        state_path = directory / "states" / f"{chunk_id}_response.json"
        saved = read(state_path)
        state_prompt = (directory / "snapshot/state_system_prompt.txt").read_text(encoding="utf-8")
        if saved["payload_hash"] != data_hash(request) or saved["prompt_hash"] != data_hash(state_prompt):
            raise ValueError("Executed state request differs from saved input")
        previous = saved["response"]
        check_state(previous, chunks[:index + 1], commit["after"])
        states.append({"after_chunk_id": chunk_id, "response_sha256": digest(state_path),
            "recursive_input_matched": True, "no_future_source": True, "graph_references_valid": True,
            "tokens": sum(x.get("usage", {}).get("total_tokens", 0) for x in saved["requests"]),
            "api_attempts": len(saved["requests"]),
            "latency_seconds": sum(x["latency_seconds"] for x in saved["requests"])})
    return {"scope": "Recursive lineage and output-reference audit, not semantic or final-graph certification",
            "states": states, "responses": responses,
            "all_reference_checks_passed": all(not row["reference_issues"] for row in responses),
            "completed": manifest["status"] == "completed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    write_json(args.directory / "request_audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
