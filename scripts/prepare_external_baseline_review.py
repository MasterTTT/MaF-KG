"""Export label-masked native predictions and explicit semantic review templates."""
import argparse
import copy
import json
import random
from pathlib import Path

from external_baseline_runtime import sha256
from prospective_native_client import write


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k not in {"embedding", "embeddings", "diagnostics"}}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def native_items(graph):
    if isinstance(graph, list):
        items, dropped = [], []
        for section, triples in enumerate(graph, 1):
            for index, triple in enumerate(triples, 1):
                if triple is None:
                    dropped.append({"section": section, "index": index, "value": None})
                else:
                    if not isinstance(triple, list) or len(triple) != 3:
                        raise ValueError("Invalid native EDC triplet")
                    items.append({"item_id": f"T{section:03d}_{index:03d}",
                                  "kind": "assertion", "native": triple})
        return items, dropped
    if not isinstance(graph, dict):
        raise ValueError("Unknown graph format")
    node_key, edge_key = ("nodes", "relations") if "nodes" in graph else ("entities", "relationships")
    items = [{"item_id": f"N{i:03d}", "kind": "entity", "native": compact(n)}
             for i, n in enumerate(graph[node_key], 1)]
    items += [{"item_id": f"R{i:03d}", "kind": "assertion", "native": compact(n)}
              for i, n in enumerate(graph[edge_key], 1)]
    return items, []


def prepare(run: Path, output: Path, *, refresh=False):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    reference = json.loads((Path(manifest["snapshot"]) / "semantic_reference.json").read_text(encoding="utf-8"))
    jobs = copy.deepcopy(manifest["jobs"])
    random.Random(20261008).shuffle(jobs)
    if refresh:
        previous = json.loads((output / "mapping_private.json").read_text(encoding="utf-8"))
        expected = [{"label": f"P{i:02d}", **job} for i, job in enumerate(jobs, 1)]
        keys = ("label", "method", "section", "repeat", "directory")
        if (len(previous) != len(expected) or
                any(any(old[k] != new[k] for k in keys) for old, new in zip(previous, expected))):
            raise ValueError("Refresh cannot change the frozen job-to-label mapping")
        if any(old["status"] in {"completed", "failed", "gated"} and old["status"] != new["status"]
               for old, new in zip(previous, expected)):
            raise ValueError("Refresh cannot replace a terminal execution outcome")
    else:
        output.mkdir(parents=True, exist_ok=False)
    mapping = []
    for index, job in enumerate(jobs, 1):
        label = f"P{index:02d}"
        mapping.append({"label": label, **job})
        if job["status"] != "completed":
            write(output / f"{label}.json", {"label": label, "status": job["status"], "not_a_zero_score": True})
            continue
        path = Path(job["directory"]) / "graph.json"
        if sha256(path) != job["graph_sha256"]:
            raise ValueError("Prediction changed after execution")
        graph = json.loads(path.read_text(encoding="utf-8"))
        items, dropped = native_items(graph)
        nodes = [n for n in reference["nodes"] if n["section"] == job["section"]]
        ids = {n["id"] for n in nodes}
        relations = [r for r in reference["relations"] if r["start"] in ids and r["end"] in ids]
        packet = {"label": label, "section": job["section"], "items": items, "native_nulls": dropped,
            "reference": {"nodes": nodes, "relations": relations},
            "policy": "Judge only actual native semantic output. Source verifies support but cannot fill absent conditions.",
            "blinding_limit": "Method labels masked; representation may reveal method. Not independent human annotation.",
            "prediction_sha256": sha256(path)}
        packet_path = output / f"{label}.json"
        review_path = output / f"{label}_review.json"
        if packet_path.exists():
            old_packet = json.loads(packet_path.read_text(encoding="utf-8"))
            if "items" in old_packet:
                if old_packet != packet:
                    raise ValueError("Refresh cannot change a completed semantic packet")
                continue
        if review_path.exists():
            raise ValueError("Refusing to overwrite existing judgments")
        write(packet_path, packet)
        write(review_path, {
            "label": label, "reviewer_provenance": "pending",
            "node_coverage": [{"reference_id": n["id"], "status": "pending", "item_ids": [],
                               "conditions_complete": None, "reason": ""} for n in nodes],
            "relation_coverage": [{"reference_id": r["id"], "status": "pending", "item_ids": [],
                                   "reason": ""} for r in relations],
            "prediction_support": [{"item_id": i["item_id"], "status": "pending", "scope_admitted": None,
                                    "reason": ""} for i in items],
            "duplicate_groups": [], "false_merges": []})
    write(output / "mapping_private.json", mapping)
    write(output / "review_policy.json", {
        "coverage_choices": ["full", "partial", "name_only", "missing", "not_applicable"],
        "support_choices": ["yes", "partial", "no", "undecided"],
        "primary": "Full required content recall; partial and name-only identification reported separately",
        "conditions": "conditions_complete true requires actual output's material mathematical restrictions",
        "no_f1": "Content recall and native-unit prediction support use different universes",
        "provenance": "AI-assisted development audit; user-approved reference is inherited, not new expert Gold",
        "native_entities": "Formal symbols needed to express a general statement are not automatically example-local objects. Do not impose our Concept/Claim/Method ontology on foreign graphs.",
        "status": "packets_prepared_not_scored"})
    print(f"Prepared {sum(j['status']=='completed' for j in jobs)} completed prediction packets")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--refresh", action="store_true", help="Add newly completed packets; preserve all judgments")
    args = p.parse_args()
    prepare(args.run.resolve(), args.output.resolve(), refresh=args.refresh)
