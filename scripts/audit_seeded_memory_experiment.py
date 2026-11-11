"""Audit incremental identity decisions without pretending IDs prove semantics."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from run_seeded_memory_experiment import audit_artifacts, digest, read, write


def edge_key(edge: dict) -> tuple:
    return edge["source_id"], edge["type"], edge["target_id"]


def evidence_key(evidence: dict) -> tuple:
    return tuple(evidence.get(key) for key in ("source_path", "start_line", "end_line", "text"))


def audit_stage(seed: dict, graph: dict, chunks: list[dict], responses: dict,
                requests: dict, initial_seed_ids: set[str]) -> dict:
    before = {node["id"]: node for node in seed["nodes"]}
    after = {node["id"]: node for node in graph["nodes"]}
    known = set(before)
    decisions = {(row["chunk_id"], row["ref_id"]): row
                 for row in graph["diagnostics"]["memory_decisions"]}
    counts = Counter()
    ledger = []
    direct_endpoints = []
    exposures = []
    for chunk in chunks:
        chunk_id = chunk["id"]
        payload = requests[chunk_id]
        exposed = {node["id"] for node in payload["node_memory"]}
        exposures.append({"chunk_id": chunk_id, "node_ids": sorted(exposed),
                          "initial_seed_ids": sorted(exposed & initial_seed_ids)})
        chunk_seen = set()
        for action in responses[chunk_id]["node_actions"]:
            decision = decisions[(chunk_id, action["ref_id"])]
            node_id = decision["node_id"]
            if node_id in initial_seed_ids:
                origin = "initial_seed"
            elif node_id in before:
                origin = "previous_section"
            elif node_id in known:
                origin = "earlier_chunk"
            elif node_id in chunk_seen:
                origin = "same_chunk"
            else:
                origin = "new_identity"
            counts[f"raw_{action['action']}"] += 1
            if action["action"] == "reuse":
                if action["existing_id"] != node_id or node_id not in exposed:
                    raise ValueError("Explicit reuse does not agree with provided memory")
                counts[f"explicit_reuse_{origin}"] += 1
            elif decision["action"] == "semantic_reuse":
                counts["deterministic_create_collapse"] += 1
                counts[f"create_collapse_{origin}"] += 1
                counts["create_collapse_exposed" if node_id in exposed else "create_collapse_not_exposed"] += 1
            elif decision["action"] != "create":
                raise ValueError("Unexpected create decision")
            chunk_seen.add(node_id)
            ledger.append({"chunk_id": chunk_id, "ref_id": action["ref_id"],
                           "raw_action": action["action"], "merged_action": decision["action"],
                           "node_id": node_id, "name": after[node_id]["name"], "origin": origin,
                           "was_exposed": node_id in exposed, "description_action": decision["description_action"],
                           "mention": decision["mention"], "reason": decision["reason"],
                           "evidence": action["evidence"]})
        for relation in responses[chunk_id].get("relations", []):
            for field in ("source_ref", "target_ref"):
                node_id = relation[field]
                if node_id not in exposed:
                    continue
                origin = ("initial_seed" if node_id in initial_seed_ids else
                          "previous_section" if node_id in before else "earlier_chunk")
                counts["direct_memory_endpoint_occurrences"] += 1
                counts[f"direct_memory_endpoint_{origin}"] += 1
                direct_endpoints.append({"chunk_id": chunk_id, "node_id": node_id,
                                         "name": after[node_id]["name"], "field": field,
                                         "relation_type": relation["relation_type"], "origin": origin,
                                         "evidence": relation["evidence"]})
        known.update(chunk_seen)
    before_edges = {edge_key(edge) for edge in seed["relations"]}
    after_edges = {edge_key(edge) for edge in graph["relations"]}
    new_edges = after_edges - before_edges
    added_ids = set(after) - set(before)
    changed = []
    lost_evidence = []
    for node_id in sorted(set(before) & set(after)):
        fields = [field for field in ("name", "type", "description", "claim_kind", "logical_form")
                  if before[node_id].get(field) != after[node_id].get(field)]
        if fields:
            changed.append({"id": node_id, "name": after[node_id]["name"], "changed_fields": fields,
                            "before": {field: before[node_id].get(field) for field in fields},
                            "after": {field: after[node_id].get(field) for field in fields}})
        absent = ({evidence_key(row) for row in before[node_id].get("evidence", [])} -
                  {evidence_key(row) for row in after[node_id].get("evidence", [])})
        if absent:
            lost_evidence.append({"id": node_id, "name": before[node_id]["name"], "count": len(absent)})
    return {
        "counts": dict(counts), "decision_ledger": ledger, "memory_exposures": exposures,
        "direct_memory_endpoints": direct_endpoints,
        "new_node_count": len(added_ids), "new_nodes": [after[key] for key in sorted(added_ids)],
        "new_relation_count": len(new_edges),
        "new_cross_store_relations": [{"source": after[source]["name"], "type": rel_type,
                                       "target": after[target]["name"], "source_id": source, "target_id": target}
                                      for source, rel_type, target in sorted(new_edges)
                                      if (source in added_ids) != (target in added_ids)],
        "new_relations": [{"source": after[source]["name"], "type": rel_type,
                           "target": after[target]["name"], "source_id": source, "target_id": target}
                          for source, rel_type, target in sorted(new_edges)],
        "missing_prior_node_ids": sorted(set(before) - set(after)),
        "prior_node_field_changes": changed, "lost_prior_node_evidence": lost_evidence,
        "removed_prior_relation_count": len(before_edges - after_edges),
        "removed_prior_relations": [list(key) for key in sorted(before_edges - after_edges)],
        "api_summary": graph["diagnostics"]["api_summary"],
        "limitation": "Mechanical identity/provenance counts; semantic duplication and false reuse need review.",
    }


def audit(output: Path) -> dict:
    manifest = read(output / "manifest.json")
    audit_artifacts(manifest)
    if any(arm["status"] != "completed" for arm in manifest["arms"]):
        raise ValueError("All arms must complete before paired scoring")
    initial = read(Path(manifest["seed"]))
    initial_ids = {node["id"] for node in initial["nodes"]}
    result = {"schema": "maf-kg.seeded-memory-audit.v1", "arms": {},
              "auditor_sha256": digest(Path(__file__)),
              "completed_manifest_sha256": digest(output / "manifest.json")}
    for arm in manifest["arms"]:
        previous = initial
        previous_path = Path(manifest["seed"])
        stages = {}
        total = Counter()
        for section in manifest["sections"]:
            directory = output / arm["name"] / section["id"]
            completion = read(directory / "completion.json")
            seed_used = read(directory / "seed_used.json")
            if completion["graph_sha256"] != digest(directory / "graph.json"):
                raise ValueError("Completed graph changed after extraction")
            if seed_used != {"path": str(previous_path), "sha256": digest(previous_path)}:
                raise ValueError("Stage did not load the expected preceding graph")
            graph = read(directory / "graph.json")
            chunks = read(Path(section["chunks"]))
            responses = {chunk["id"]: read(directory / "responses" / f"{chunk['id']}.json") for chunk in chunks}
            requests = {}
            for chunk in chunks:
                request = read(directory / "requests" / f"{chunk['id']}.json")
                user = next(row["content"] for row in request["body"]["messages"] if row["role"] == "user")
                requests[chunk["id"]] = json.loads(user)
            stage = audit_stage(previous, graph, chunks, responses, requests, initial_ids)
            stage["source_line_offset"] = section["source_line_offset"]
            write(directory / "mechanical_audit.json", stage)
            stages[section["id"]] = stage
            total.update(stage["counts"])
            previous = graph
            previous_path = directory / "graph.json"
        result["arms"][arm["name"]] = {
            "counts": dict(total), "stages": stages,
            "final_nodes": len(previous["nodes"]), "final_relations": len(previous["relations"]),
            "distinct_initial_seed_attached_ids": sorted({row["node_id"] for stage in stages.values()
                for row in stage["decision_ledger"] if row["origin"] == "initial_seed"}),
        }
    # Freeze the treatment-independent part of each model input as a paired check.
    for section in manifest["sections"]:
        left = result["arms"]["no_visible_memory"]["stages"][section["id"]]
        right = result["arms"]["visible_memory"]["stages"][section["id"]]
        for chunk in read(Path(section["chunks"])):
            values = []
            bodies = []
            for arm in ("no_visible_memory", "visible_memory"):
                request = read(output / arm / section["id"] / "requests" / f"{chunk['id']}.json")
                value = json.loads(next(row["content"] for row in request["body"]["messages"] if row["role"] == "user"))
                value.pop("node_memory")
                value.pop("relation_memory")
                values.append(value)
                body = request["body"]
                body["messages"] = [row for row in body["messages"] if row["role"] != "user"]
                bodies.append({"endpoint": request["endpoint"], "provider": request["provider"], "body": body})
            if values[0] != values[1]:
                raise ValueError("Non-memory model context differs between arms")
            if bodies[0] != bodies[1]:
                raise ValueError("Non-memory request settings differ between arms")
        left["paired_non_memory_context_equal"] = right["paired_non_memory_context_equal"] = True
    write(output / "mechanical_metrics.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = audit(args.output_dir.resolve())
    for name, arm in result["arms"].items():
        print(name, arm["counts"], f"final={arm['final_nodes']}/{arm['final_relations']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
