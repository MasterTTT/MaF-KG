"""Describe executed context size and committed updates, without semantic scoring."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from extraction_context_state import compact_bytes
from run_context_state_experiment import validate_run
from run_memory_ablation import digest, write_json


def summarize(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if any(arm["status"] != "completed" for arm in manifest["arms"]):
        raise ValueError("All arms must complete before context statistics")
    chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    result = {"topic": manifest["topic"], "status": "descriptive_not_semantic_quality",
              "script_sha256": digest(Path(__file__)), "arms": {}}
    for arm in manifest["arms"]:
        target = directory / arm["name"]
        graph = json.loads((target / "graph.json").read_text(encoding="utf-8"))
        if digest(target / "graph.json") != arm["graph_sha256"]:
            raise ValueError("Graph differs from completed experiment")
        validate_run(directory, manifest, arm, graph)
        rows = []
        committed_events = Counter()
        displayed_events = Counter()
        for chunk in chunks:
            request_path = target / "requests" / (chunk["id"] + ".json")
            commit_path = target / "commits" / (chunk["id"] + ".json")
            request = json.loads(request_path.read_text(encoding="utf-8"))
            commit = json.loads(commit_path.read_text(encoding="utf-8"))
            legacy = request["legacy_payload"]
            sent = request["sent_payload"]
            nodes, edges = legacy["node_memory"], legacy["relation_memory"]
            updates = sent.get("graph_state", {}).get("recent_updates", [])
            committed_events.update(event["kind"] for event in commit["events"])
            displayed_events.update(event["kind"] for event in updates)
            before_ids = set(commit["before"]["nodes"])
            added_from_existing = [event for event in commit["events"]
                                   if event["kind"] == "relation_added" and event["source_id"] in before_ids]
            rows.append({
                "chunk_id": chunk["id"], "request_sha256": digest(request_path),
                "commit_sha256": digest(commit_path),
                "memory_node_count": len(nodes), "memory_relation_count": len(edges),
                "node_memory_item_bytes": sum(compact_bytes(node) for node in nodes),
                "relation_memory_item_bytes": sum(compact_bytes(edge) for edge in edges),
                "memory_description_characters": sum(len(node["short_description"]) for node in nodes),
                "descriptions_over_100_characters": sum(len(node["short_description"]) > 100 for node in nodes),
                "actual_user_payload_bytes": compact_bytes(sent),
                "displayed_recent_events": len(updates),
                "omitted_relevant_recent_events": sent.get("graph_state", {}).get("recent_updates_scope", {}).get("omitted_relevant_events", 0),
                "added_edges_from_preexisting_sources": len(added_from_existing),
                "added_edges_from_preexisting_source_identities": added_from_existing,
            })
        endpoints = {endpoint for edge in graph["relations"] for endpoint in (edge["source_id"], edge["target_id"])}
        result["arms"][arm["name"]] = {
            "graph_sha256": arm["graph_sha256"], "chunk_count": len(chunks), "chunks": rows,
            "total_selected_node_exposures": sum(row["memory_node_count"] for row in rows),
            "total_selected_relation_exposures": sum(row["memory_relation_count"] for row in rows),
            "total_user_payload_bytes": sum(row["actual_user_payload_bytes"] for row in rows),
            "committed_event_counts": dict(committed_events),
            "displayed_event_exposures": dict(displayed_events),
            "added_edges_from_preexisting_sources": sum(row["added_edges_from_preexisting_sources"] for row in rows),
            "isolated_node_ids": sorted(node["id"] for node in graph["nodes"] if node["id"] not in endpoints),
            "api_summary": graph["diagnostics"]["api_summary"],
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.run_dir.resolve())
    write_json(args.run_dir / "context_statistics.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
