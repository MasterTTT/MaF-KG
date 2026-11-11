"""Additive semantic-review summaries and topology diagnostics, never rescore Gold."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict, deque
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fraction(hit: int, total: int):
    return {"hit": hit, "total": total, "rate": hit / total if total else None}


def topology(graph: dict, exclude: set[str] | None = None) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("Duplicate node ID")
    incoming, outgoing, neighbors = (defaultdict(set) for _ in range(3))
    triples = Counter()
    types = Counter()
    invalid, self_loops = [], []
    for relation in graph["relations"]:
        if relation["type"] in (exclude or set()):
            continue
        source, target = relation["source_id"], relation["target_id"]
        if source not in nodes or target not in nodes:
            invalid.append(relation["id"])
            continue
        triples[(source, relation["type"], target)] += 1
        types[relation["type"]] += 1
        if source == target:
            self_loops.append(relation["id"])
            continue
        outgoing[source].add(target)
        incoming[target].add(source)
        neighbors[source].add(target)
        neighbors[target].add(source)
    remaining = set(nodes)
    components = []
    while remaining:
        start = min(remaining)
        queue = deque([start])
        remaining.remove(start)
        group = [start]
        while queue:
            for other in sorted(neighbors[queue.popleft()]):
                if other in remaining:
                    remaining.remove(other)
                    queue.append(other)
                    group.append(other)
        components.append(sorted(group))
    components.sort(key=lambda c: (-len(c), c))
    isolated = [n for n in sorted(nodes) if not neighbors[n]]
    degrees = sorted(len(neighbors[n]) for n in nodes)
    pair_count = sum(len(v) for v in outgoing.values())
    return {
        "node_count": len(nodes), "valid_relation_records": sum(types.values()),
        "relation_types": dict(types), "isolated": fraction(len(isolated), len(nodes)),
        "isolated_nodes": [{"id": n, "name": nodes[n]["name"], "type": nodes[n]["type"],
                            "evidence": nodes[n].get("evidence", [])} for n in isolated],
        "weak_component_count": len(components), "component_sizes": [len(c) for c in components],
        "largest_component": fraction(len(components[0]) if components else 0, len(nodes)),
        "degree_mean": sum(degrees) / len(degrees) if degrees else None,
        "degree_median": ((degrees[(len(degrees) - 1) // 2] + degrees[len(degrees) // 2]) / 2
                          if degrees else None),
        "degree_max": max(degrees, default=0),
        "simple_directed_density": pair_count / (len(nodes) * (len(nodes) - 1)) if len(nodes) > 1 else None,
        "missing_endpoint_relation_ids": invalid, "self_loop_relation_ids": self_loops,
        "duplicate_triple_extra": sum(v - 1 for v in triples.values()),
        "by_node_type": {typ: {
            "nodes": sum(n["type"] == typ for n in nodes.values()),
            "isolated": sum(nodes[n]["type"] == typ for n in isolated)}
            for typ in sorted({n["type"] for n in nodes.values()})},
    }


def semantic(rows: list[dict]) -> dict:
    counts = Counter()
    groups = defaultdict(Counter)
    relations = Counter()
    for row in rows:
        counts.update(row["counts"])
        for node in row["gold_reviews"]:
            if node["requirement"] != "M":
                continue
            for group in ("all", "type:" + node["type"], "tier:" + node["tier"]):
                groups[group]["total"] += 1
                groups[group]["identity"] += node["identity_id"] is not None
                groups[group]["content"] += bool(node["content_ids"])
                groups[group]["description"] += node["description_complete"]
                groups[group]["checklist"] += node["conditions_complete"]
        for relation in row["provisional_relation_checks"]:
            relations["total"] += 1
            relations["hit"] += relation["hit"]
            relations["available"] += relation["endpoints_available"]
    return {
        "node_review_acceptance": fraction(counts["node_valid"], counts["nodes"]),
        "node_review_uncertain": counts["node_uncertain"],
        "relation_review_acceptance": fraction(counts["relation_valid"], counts["relations"]),
        "relation_review_uncertain": counts["relation_uncertain"],
        "required_groups": {g: {metric: fraction(c[metric], c["total"])
                                for metric in ("identity", "content", "description", "checklist")}
                            for g, c in groups.items()},
        "strict_reference_relation_recall": fraction(relations["hit"], relations["total"]),
        "endpoint_conditional_relation_recall": fraction(relations["hit"], relations["available"]),
        "duplicate_extra_nodes": counts["duplicate_extra_nodes"], "false_merges": counts["false_merges"],
        "f1": None,
        "f1_reason": "Prediction validity review and finite non-exhaustive reference recall have different universes; do not combine into F1.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--historical-graph", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = read(args.experiment / "manifest.json")
    metrics_path = args.experiment / "reviewed_metrics.json"
    metrics = read(metrics_path)
    if any(j["status"] != "completed" for j in manifest["jobs"]) or len(metrics["jobs"]) != 12:
        raise ValueError("Incomplete paired experiment")
    artifacts = [{"path": str(metrics_path.resolve()), "sha256": digest(metrics_path)},
                 {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__))}]
    reference = read(Path(manifest["reference"]))
    relation_types = {r["id"]: r["type"] for r in reference["relations_provisional"]}
    jobs = []
    for job in manifest["jobs"]:
        directory = Path(job["directory"])
        graph_path, review_path = directory / "graph.json", directory / "review.json"
        graph, review = read(graph_path), read(review_path)
        row = next(r for r in metrics["jobs"] if
                   (r["section"], r["repeat"], r["arm"]) == (job["section"], job["repeat"], job["name"]))
        if review["graph_sha256"] != digest(graph_path) or row["review_sha256"] != digest(review_path):
            raise ValueError("Review hash binding differs")
        if review["reference_sha256"] != digest(Path(manifest["reference"])):
            raise ValueError("Reference changed")
        artifacts.extend({"path": str(p.resolve()), "sha256": digest(p)} for p in (graph_path, review_path))
        by_relation_type = defaultdict(Counter)
        relation_by_id = {r["id"]: r for r in graph["relations"]}
        for decision in review["relation_reviews"]:
            c = by_relation_type[relation_by_id[decision["id"]]["type"]]
            c["predicted"] += 1
            c[decision["verdict"]] += 1
        for check in row["provisional_relation_checks"]:
            c = by_relation_type[relation_types[check["gold_id"]]]
            c["reference"] += 1
            c["hit"] += check["hit"]
        jobs.append({"section": job["section"], "repeat": job["repeat"], "arm": job["name"],
                     "semantic": semantic([row]), "relation_types": dict(by_relation_type),
                     "views": {"all": topology(graph), "without_involves": topology(graph, {"involves"})},
                     "evidence_field_presence": fraction(sum(bool(n.get("evidence")) for n in graph["nodes"]), len(graph["nodes"]))})
    historical = read(args.historical_graph)
    artifacts.append({"path": str(args.historical_graph.resolve()), "sha256": digest(args.historical_graph)})
    result = {"status": "assistant preliminary semantics plus automatic topology; not final human scores",
              "date": "2026-10-04", "jobs": jobs,
              "aggregate": {a: semantic([r for r in metrics["jobs"] if r["arm"] == a]) for a in ("sequential", "direct")},
              "historical_full_graph": {"path": str(args.historical_graph), "label": "historical mixed configuration, not new heading-pipeline output",
                                         "views": {"all": topology(historical), "without_involves": topology(historical, {"involves"})}},
              "input_artifacts": artifacts,
              "limits": ["Three development sections, each repeated twice; 40 required-item observations are 20 unique items.",
                         "No semantic topology threshold; isolated does not imply wrong.",
                         "Checklist includes source definitions/steps/reminders, not only necessary assumptions; absence is not automatically mathematical error.",
                         "Evidence presence is not evidence support; no new support score inferred.",
                         "No new semantic review or Gold modification; no full-book accuracy inferred from sampled sections."]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
    print("Historical topology", json.dumps({k: {n: v[n] for n in ("node_count", "valid_relation_records", "isolated", "weak_component_count", "largest_component")}
                                             for k, v in result["historical_full_graph"]["views"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
