"""Validate locked masked ratings, then unblind separate prediction-quality axes."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path


NODE_FIELDS = ("type", "name", "description", "logical_form", "aliases", "claim_kind")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def signatures(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for item in tree.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == "RELATION_SIGNATURES":
            return ast.literal_eval(item.value)
    raise ValueError("No frozen relation signatures")


def indexed(items, field="review_id"):
    result = {i[field]: i for i in items}
    require(len(result) == len(items), f"Duplicate {field}")
    return result


def validate_ratings(ratings, expected, fields, start, end):
    actual = indexed(ratings)
    require(set(actual) == set(expected), "Incomplete, extra or missing review IDs")
    for rid, row in actual.items():
        for field, values in fields.items():
            require(row.get(field) in values, f"Invalid {field} for {rid}")
        require(isinstance(row.get("reason"), str) and row["reason"].strip(), f"Missing reason for {rid}")
        lines = row.get("source_lines")
        require(isinstance(lines, list) and lines, f"Missing source lines for {rid}")
        require(all(type(n) is int and start <= n <= end for n in lines), f"Out-of-section source line for {rid}")
    return actual


def categorical(rows, field, positive, negative, na=None):
    counts = Counter(row[field] for row in rows)
    yes, no, unsure = counts[positive], counts[negative], counts["uncertain"]
    omitted = counts[na] if na else 0
    applicable = len(rows) - omitted
    return {"counts": dict(counts), "all_predictions": len(rows), "applicable": applicable,
            "resolved_denominator": yes + no, "resolved_ratio": yes/(yes+no) if yes+no else None,
            "uncertain": unsure, "not_applicable": omitted,
            "lower_bound": yes/applicable if applicable else None,
            "upper_bound": (yes+unsure)/applicable if applicable else None}


def summarize(packet_dir, review_path):
    packet_dir, review_path = Path(packet_dir), Path(review_path)
    private = read(packet_dir / "private/unblinding_map.json")
    manifest = read(private["manifest_path"])
    require(digest(private["manifest_path"]) == private["manifest_sha256"], "Final manifest changed after packet freeze")
    sigs = signatures(Path(manifest["snapshot"]) / "agents/Extractor.py")
    material = private["review_materials"]
    for key in ("protocol", "extractor_prompt"):
        require(digest(material[f"{key}_path"]) == material[f"{key}_sha256"], "Review ontology or protocol changed")
    require(digest(packet_dir / "extraction_system.txt") == material["extractor_prompt_copy_sha256"], "Public ontology copy changed")
    review = read(review_path)
    graphs = indexed(review["graphs"], "packet_id")
    bindings = private["packets"]
    require(set(graphs) == {p["packet_id"] for p in bindings}, "Review must cover every packet")
    results, all_nodes, all_edges = [], [], []
    node_fields = {"source_support": {"supported", "unsupported", "uncertain"},
        "ontology_admission": {"eligible", "ineligible", "uncertain"},
        "condition_completeness": {"complete", "incomplete", "not_applicable", "uncertain"}}
    edge_fields = {"source_support": {"supported", "unsupported", "uncertain"},
        "direction_correctness": {"correct", "incorrect", "uncertain"}, "schema_signature": {"valid", "invalid"}}
    for binding in bindings:
        for key in ("graph", "source", "input"):
            require(digest(binding[f"{key}_path"]) == binding[f"{key}_sha256"], f"Immutable {key} changed")
        pid = binding["packet_id"]
        packet_path = packet_dir / f"{pid}.json"
        packet = read(packet_path)
        original = read(binding["graph_path"])
        original_nodes = indexed(original["nodes"], "id")
        original_edges = indexed(original["relations"], "id")
        node_map = {m["review_id"]: m["original_id"] for m in binding["node_mapping"]}
        edge_map = {m["review_id"]: m["original_id"] for m in binding["relation_mapping"]}
        require(len(node_map) == len(original_nodes) and set(node_map.values()) == set(original_nodes), "Not all nodes are masked")
        require(len(edge_map) == len(original_edges) and set(edge_map.values()) == set(original_edges), "Not all edges are masked")
        nodes, edges = indexed(packet["nodes"]), indexed(packet["relations"])
        require(set(nodes) == set(node_map) and set(edges) == set(edge_map), "Packet coverage changed")
        source_lines = Path(binding["source_path"]).read_text(encoding="utf-8").splitlines()
        start, end = binding["chapter_lines"]
        expected_text = [{"line": i, "text": source_lines[i-1]} for i in range(start, end+1)]
        require(packet["section"]["textbook_lines"] == expected_text, "Authoritative packet source changed")
        for rid, node in nodes.items():
            require(node["prediction"] == {f:original_nodes[node_map[rid]].get(f) for f in NODE_FIELDS}, "Masked node changed")
        reverse_node_map = {v:k for k,v in node_map.items()}
        for rid, edge in edges.items():
            raw = original_edges[edge_map[rid]]
            require(edge["type"] == raw["type"], "Masked relation type changed")
            require(edge["source"]["review_id"] == reverse_node_map[raw["source_id"]]
                    and edge["target"]["review_id"] == reverse_node_map[raw["target_id"]], "Masked relation direction changed")
        ratings = graphs[pid]
        nr = validate_ratings(ratings["node_reviews"], nodes, node_fields, start, end)
        er = validate_ratings(ratings["relation_reviews"], edges, edge_fields, start, end)
        groups = Counter()
        for row in nr.values():
            group = row.get("duplicate_group")
            require(group is None or (isinstance(group, str) and group.startswith(pid+"-")), "Duplicate groups must be scoped to packet")
            if group is not None:
                groups[group] += 1
        require(all(n >= 2 for n in groups.values()), "Singleton is not a duplicate group")
        for rid, row in er.items():
            edge = edges[rid]
            valid = (edge["source"]["type"], edge["target"]["type"]) in sigs.get(edge["type"], ())
            require(row["schema_signature"] == ("valid" if valid else "invalid"), "Reviewer schema-signature label disagrees with frozen schema")
        nrows, erows = list(nr.values()), list(er.values())
        redundant = sum(n-1 for n in groups.values())
        results.append({"packet_id": pid, "model": binding["model"], "arm": binding["arm"],
            "section": binding["section_id"], "nodes": len(nrows), "relations": len(erows),
            "node_source_support": categorical(nrows, "source_support", "supported", "unsupported"),
            "node_admission": categorical(nrows, "ontology_admission", "eligible", "ineligible"),
            "condition_completeness": categorical(nrows, "condition_completeness", "complete", "incomplete", "not_applicable"),
            "duplicate_redundant_nodes": redundant, "duplicate_rate": redundant/len(nrows) if nrows else None,
            "duplicate_groups": dict(groups),
            "relation_source_support": categorical(erows, "source_support", "supported", "unsupported"),
            "relation_direction": categorical(erows, "direction_correctness", "correct", "incorrect"),
            "packet_sha256": digest(packet_path), "graph_sha256": binding["graph_sha256"]})
        all_nodes.extend(nrows)
        all_edges.extend(erows)
    return {"status": "complete locked assistant audit; mechanically validated, not human Gold",
        "reviewer": review["reviewer"], "prior_exposure": review.get("prior_exposure"),
        "review_sha256": digest(review_path), "private_map_sha256": digest(packet_dir / "private/unblinding_map.json"),
        "rows": sorted(results, key=lambda r:r["model"]),
        "total_nodes": len(all_nodes), "total_relations": len(all_edges),
        "aggregate_node_support": categorical(all_nodes, "source_support", "supported", "unsupported"),
        "aggregate_admission": categorical(all_nodes, "ontology_admission", "eligible", "ineligible"),
        "aggregate_conditions": categorical(all_nodes, "condition_completeness", "complete", "incomplete", "not_applicable"),
        "aggregate_relation_support": categorical(all_edges, "source_support", "supported", "unsupported"),
        "aggregate_relation_direction": categorical(all_edges, "direction_correctness", "correct", "incorrect"),
        "caveat": "Semantic labels are AI-assisted judgments; validation only establishes coverage, provenance, label legality and schema consistency. No expert accuracy, significance, F1 or universal ranking claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet_dir", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = summarize(args.packet_dir, args.review)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"nodes": result["total_nodes"], "relations": result["total_relations"]}))
