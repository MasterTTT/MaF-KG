"""Freeze scoped predictions and summarize explicit offline review decisions."""

import argparse
import hashlib
import json
import re
from pathlib import Path, PureWindowsPath


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def basename(path):
    return str(path).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def overlaps(evidence, section, allow_bare_filename=False):
    source = evidence.get("source_path", "")
    same_source = str(PureWindowsPath(source)).casefold() == str(PureWindowsPath(section["source"])).casefold()
    if allow_bare_filename and "/" not in source and "\\" not in source:
        same_source = basename(source) == basename(section["source"])
    return (
        same_source
        and evidence.get("start_line", 0) <= section["end_line"]
        and evidence.get("end_line", 0) >= section["start_line"]
    )


def local(item, section, allow_bare_filename=False):
    return any(overlaps(e, section, allow_bare_filename) for e in item.get("evidence", []))


def member_ids(unit):
    return {
        n["id"] if isinstance(n, dict) else n
        for key in ("centers_on", "contains")
        for n in unit.get(key, [])
    }


def save_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def extract(reference, output):
    for section in reference["sections"]:
        if digest(section["source"]) != section["source_sha256"]:
            raise ValueError(f"Source changed: {section['source']}")
    system = reference["system"]
    for key in ("l1", "ku"):
        if digest(system[f"{key}_path"]) != system[f"{key}_sha256"]:
            raise ValueError(f"Prediction changed: {system[f'{key}_path']}")
    graph = read_json(system["l1_path"])
    units = read_json(system["ku_path"])
    all_nodes = {n["id"]: n for n in graph["nodes"]}
    counts = {}
    evidence_audit = {"meaning": "Whitespace-compacted evidence text occurs in its cited source span; this does not establish semantic completeness.", "sections": {}}
    for section in reference["sections"]:
        nodes = [n for n in graph["nodes"] if local(n, section)]
        ids = {n["id"] for n in nodes}
        relations = [r for r in graph["relations"] if local(r, section)]
        incident = [
            r for r in graph["relations"]
            if r["source_id"] in ids or r["target_id"] in ids
        ]
        neighbors = {
            endpoint for r in relations + incident
            for endpoint in (r["source_id"], r["target_id"])
        } - ids
        ku = [
            u for u in units["knowledge_units"]
            if local(u, section, allow_bare_filename=True) or member_ids(u) & ids
        ]
        payload = {
            "section": section,
            "scope": "chapter-aware evidence overlap; incident relations separate",
            "nodes": nodes,
            "relations_local_evidence": relations,
            "relations_incident": incident,
            "neighbor_nodes": [all_nodes[i] for i in sorted(neighbors)],
            "math_expressions": [
                e for e in graph.get("math_expressions", [])
                if e.get("owner_id") in ids | neighbors or local(e, section)
            ],
            "knowledge_units": ku,
        }
        save_json(output / f"{section['id']}_predictions.json", payload)
        lines = Path(section["source"]).read_text(encoding="utf-8").splitlines()
        checks = []
        for category, items in (("node", nodes), ("relation", relations)):
            for item in items:
                for e in item.get("evidence", []):
                    if not overlaps(e, section):
                        continue
                    source_text = "\n".join(lines[e["start_line"] - 1:e["end_line"]])
                    checks.append({"category": category, "id": item["id"], "start_line": e["start_line"], "end_line": e["end_line"], "text_in_span": re.sub(r"\s+", "", e["text"]) in re.sub(r"\s+", "", source_text)})
        evidence_audit["sections"][section["id"]] = checks
        counts[section["id"]] = {
            "nodes_local": len(nodes), "relations_local": len(relations),
            "relations_incident": len(incident), "neighbor_nodes": len(neighbors),
            "knowledge_units_candidates": len(ku),
        }
    save_json(output / "scope_counts.json", counts)
    save_json(output / "evidence_audit.json", evidence_audit)
    return counts


def validate_alignments(aligned, expected, reference_ids):
    predictions = [a["prediction_id"] for a in aligned]
    references = [a["reference_id"] for a in aligned]
    if len(set(predictions)) != len(predictions) or len(set(references)) != len(references):
        raise ValueError("Alignment must be one-to-one")
    if not set(predictions) <= expected:
        raise ValueError("Alignment refers to a prediction outside the reviewed scope")
    if not set(references) <= reference_ids:
        raise ValueError("Unknown reference endpoint")
    if any(type(a.get("accepted")) is not bool for a in aligned):
        raise ValueError("Every alignment requires an explicit boolean accepted verdict")


def score(reference, reference_path, review, review_path, output):
    if review["reference_sha256"] != digest(reference_path):
        raise ValueError("Review reference hash mismatch")
    metrics = {}
    global_node_ids = set()
    for section in reference["sections"]:
        sid = section["id"]
        scope = read_json(output / f"{sid}_predictions.json")
        decisions = review["sections"][sid]
        node_decisions = decisions["nodes"]
        expected = {n["id"] for n in scope["nodes"]}
        reviewed = {n["prediction_id"] for n in node_decisions}
        if len(reviewed) != len(node_decisions) or reviewed != expected:
            raise ValueError(f"Incomplete or repeated node review: {sid}")
        required = {n["id"] for n in reference["nodes"] if n["section"] == sid and n["status"] == "M"}
        optional = {n["id"] for n in reference["nodes"] if n["section"] == sid and n["status"] == "O"}
        aligned = decisions["alignments"]
        validate_alignments(aligned, expected, required | optional)
        hits = {a["reference_id"] for a in aligned if a["accepted"]}
        verdicts = {n["prediction_id"]: n["verdict"] for n in node_decisions}
        if any(a["accepted"] and verdicts[a["prediction_id"]] != "valid" for a in aligned):
            raise ValueError(f"Accepted alignment contradicts node review: {sid}")
        recoveries = decisions.get("content_recoveries", [])
        if any(r["reference_id"] not in required - hits or r["prediction_id"] not in expected for r in recoveries):
            raise ValueError(f"Invalid content-only recovery: {sid}")
        content_hits = hits | {r["reference_id"] for r in recoveries}
        relation_decisions = decisions["relations"]
        rel_ids = {r["id"] for r in scope["relations_local_evidence"]}
        if {r["prediction_id"] for r in relation_decisions} != rel_ids or len(relation_decisions) != len(rel_ids):
            raise ValueError(f"Incomplete or repeated relation review: {sid}")
        node_valid = sum(n["verdict"] == "valid" for n in node_decisions)
        node_uncertain = sum(n["verdict"] == "uncertain" for n in node_decisions)
        rel_valid = sum(r["verdict"] == "valid" for r in relation_decisions)
        rel_uncertain = sum(r["verdict"] == "uncertain" for r in relation_decisions)
        ku_audit = decisions["knowledge_units"]
        ku_references = {u["id"] for u in reference["knowledge_units"] if u["section"] == sid}
        ku_predictions = {u["id"]: u for u in scope["knowledge_units"]}
        ku_matches = ku_audit["matches"]
        if len({m["reference_id"] for m in ku_matches}) != len(ku_matches) or len({m["prediction_id"] for m in ku_matches}) != len(ku_matches):
            raise ValueError(f"KU alignment must be one-to-one: {sid}")
        if any(m["reference_id"] not in ku_references or m["prediction_id"] not in ku_predictions or type(m.get("objective_match")) is not bool for m in ku_matches):
            raise ValueError(f"Invalid KU alignment: {sid}")
        covered = set().union(*(member_ids(ku_predictions[m["prediction_id"]]) for m in ku_matches))
        if ku_audit["target_hits"] != sum(m["objective_match"] for m in ku_matches) or ku_audit["target_total"] != len(ku_references) or ku_audit["local_member_coverage"] != len(expected & covered):
            raise ValueError(f"KU count contradicts explicit memberships: {sid}")
        missing = sorted(required - hits)
        metrics[sid] = {
            "required_hits": len(required & hits), "required_total": len(required),
            "required_recall": len(required & hits) / len(required),
            "content_hits": len(required & content_hits),
            "content_recall": len(required & content_hits) / len(required),
            "by_type": {
                kind: {
                    "hits": sum(n["id"] in hits for n in reference["nodes"] if n["section"] == sid and n["status"] == "M" and n["type"] == kind),
                    "total": sum(n["section"] == sid and n["status"] == "M" and n["type"] == kind for n in reference["nodes"]),
                } for kind in ("Concept", "Claim", "Method")
            },
            "optional_hits": len(optional & hits), "optional_total": len(optional),
            "missing_required": missing,
            "local_predictions": len(node_decisions), "node_valid": node_valid,
            "node_invalid": len(node_decisions) - node_valid - node_uncertain,
            "node_uncertain": node_uncertain,
            "node_valid_rate_lower": node_valid / len(node_decisions),
            "node_valid_rate_upper": (node_valid + node_uncertain) / len(node_decisions),
            "relations_local": len(relation_decisions), "relation_valid": rel_valid,
            "relation_invalid": len(relation_decisions) - rel_valid - rel_uncertain,
            "relation_uncertain": rel_uncertain,
            "relation_valid_rate_lower": rel_valid / len(relation_decisions),
            "relation_valid_rate_upper": (rel_valid + rel_uncertain) / len(relation_decisions),
            "ku_audit": ku_audit,
        }
        global_node_ids.update(expected)
    total_required = sum(v["required_total"] for v in metrics.values())
    total_local = sum(v["local_predictions"] for v in metrics.values())
    total_rel = sum(v["relations_local"] for v in metrics.values())
    aggregate = {
        "required_hits": sum(v["required_hits"] for v in metrics.values()),
        "required_total": total_required,
        "required_micro_recall": sum(v["required_hits"] for v in metrics.values()) / total_required,
        "required_macro_recall": sum(v["required_recall"] for v in metrics.values()) / len(metrics),
        "content_hits": sum(v["content_hits"] for v in metrics.values()),
        "content_micro_recall": sum(v["content_hits"] for v in metrics.values()) / total_required,
        "local_predictions_sum": total_local, "local_predictions_unique": len(global_node_ids),
        "node_valid": sum(v["node_valid"] for v in metrics.values()),
        "node_invalid": sum(v["node_invalid"] for v in metrics.values()),
        "node_uncertain": sum(v["node_uncertain"] for v in metrics.values()),
        "relations_local_sum": total_rel,
        "relation_valid": sum(v["relation_valid"] for v in metrics.values()),
        "relation_invalid": sum(v["relation_invalid"] for v in metrics.values()),
        "relation_uncertain": sum(v["relation_uncertain"] for v in metrics.values()),
    }
    alignment_lookup = {
        a["reference_id"]: a["prediction_id"]
        for s in review["sections"].values() for a in s["alignments"] if a["accepted"]
    }
    source_graph = read_json(reference["system"]["l1_path"])
    graph_relations = {
        (r["source_id"], r["type"], r["target_id"])
        for r in source_graph["relations"]
    }
    relation_hits = []
    for r in reference["relations_provisional"]:
        start, end = alignment_lookup.get(r["start"]), alignment_lookup.get(r["end"])
        relation_hits.append({
            "id": r["id"], "endpoints_available": bool(start and end),
            "hit": bool(start and end and (start, r["type"], end) in graph_relations),
        })
    aggregate["provisional_reference_relations_hits"] = sum(r["hit"] for r in relation_hits)
    aggregate["provisional_reference_relations_total"] = len(relation_hits)
    aggregate["provisional_reference_relations_endpoints_available"] = sum(r["endpoints_available"] for r in relation_hits)
    save_json(output / "metrics.json", {
        "status": "assistant-reviewed preliminary audit",
        "provenance": {"reference_path": str(reference_path), "reference_sha256": digest(reference_path), "review_path": str(review_path), "review_sha256": digest(review_path), "system": reference["system"], "recorded_final_run_config": {k: v for k, v in source_graph.get("diagnostics", {}).get("run_config", {}).items() if k in ("run_started_at", "run_finished_at", "provider", "model_requested", "thinking", "reasoning_effort_requested", "reasoning_effort_sent", "chunk_strategy", "extractor_sha256", "system_prompt_sha256")}, "reference_status": reference["status"], "approval_basis": reference["approval_basis"], "scoring_policy": reference["scoring_policy"]},
        "sections": metrics, "aggregate": aggregate,
        "provisional_reference_relations": relation_hits,
    })
    return {"sections": metrics, "aggregate": aggregate}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    reference = read_json(args.reference)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    counts = extract(reference, args.output_dir)
    if args.review:
        result = score(reference, args.reference, read_json(args.review), args.review, args.output_dir)
    else:
        result = counts
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
