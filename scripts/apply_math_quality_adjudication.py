"""Apply explicit user decisions as an additive evaluation overlay, never edit predictions."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from evaluate_mathematical_graph_quality import digest, fraction, read
import json


def apply_decision(kind: str, decision: str) -> str:
    if kind == "node":
        return {
            "allow_optional_support": "valid",
            "allow_explicit_condition_claim": "valid",
            "reject_separate_method": "admission_issue",
            "accept_body_flag_metadata": "body_valid_metadata_unsupported",
        }[decision]
    return {
        "allow_optional_support": "valid",
        "allow_explicit_condition_claim": "valid",
        "reject_separate_method": "endpoint_admission_issue",
        "reject_relation_type": "relation_type_issue",
    }[decision]


def summarize(rows: list[dict]) -> dict:
    counts = {kind: Counter(d["adjudicated_verdict"] for r in rows for d in r[kind + "_reviews"])
              for kind in ("node", "relation")}
    node, relation = counts["node"], counts["relation"]
    return {"node_verdicts": dict(node), "relation_verdicts": dict(relation),
            "node_fully_accepted": fraction(node["valid"], sum(node.values())),
            "node_body_and_admission_accepted": fraction(
                node["valid"] + node["body_valid_metadata_unsupported"], sum(node.values())),
            "relation_fully_accepted": fraction(relation["valid"], sum(relation.values())),
            "relation_accepted_given_admitted_endpoints": fraction(
                relation["valid"], sum(relation.values()) - relation["endpoint_admission_issue"]),
            "unknown_total": node["uncertain"] + relation["uncertain"],
            "note": "Validity ratios over all predictions, not Gold-aligned F1. Conditional ratio excludes endpoint admission issues explicitly."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    baseline = read(args.baseline)
    for artifact in baseline["input_artifacts"]:
        if digest(Path(artifact["path"])) != artifact["sha256"]:
            raise ValueError("Baseline artifact changed")
    decisions = read(args.decisions)
    manifest = read(args.experiment / "manifest.json")
    rules = {}
    for rule in decisions["decisions"]:
        for kind in ("node", "relation"):
            for item_id in rule[kind + "_ids"]:
                key = kind, item_id
                if key in rules:
                    raise ValueError("Overlapping adjudication targets")
                rules[key] = rule
    applied = Counter()
    jobs = []
    for job in manifest["jobs"]:
        directory = Path(job["directory"])
        review = read(directory / "review.json")
        if review["graph_sha256"] != digest(directory / "graph.json"):
            raise ValueError("Review graph binding changed")
        row = {"section": job["section"], "repeat": job["repeat"], "arm": job["name"],
               "graph_sha256": digest(directory / "graph.json"),
               "original_review_sha256": digest(directory / "review.json")}
        for kind in ("node", "relation"):
            items = []
            for item in review[kind + "_reviews"]:
                key = kind, item["id"]
                updated = {**item, "adjudicated_verdict": item["verdict"],
                           "adjudication_source": "unchanged assistant preliminary review"}
                if key in rules:
                    if item["verdict"] != "uncertain":
                        raise ValueError("Expected disputed target has another original status")
                    rule = rules[key]
                    updated.update({"adjudicated_verdict": apply_decision(kind, rule["decision"]),
                                    "adjudication_id": rule["id"], "adjudication_reason": rule["reason"],
                                    "adjudication_source": "user approved five-group decision"})
                    applied[key] += 1
                items.append(updated)
            row[kind + "_reviews"] = items
        jobs.append(row)
    if set(applied) != set(rules) or any(value != 1 for value in applied.values()):
        raise ValueError("Adjudication target coverage is not exact")
    result = {"schema": "maf-kg.math-quality.adjudicated-overlay.v1",
              "status": "five groups user-adjudicated; remaining reviews still assistant preliminary",
              "baseline_sha256": digest(args.baseline), "decisions": decisions,
              "decisions_sha256": digest(args.decisions), "script_sha256": digest(Path(__file__)),
              "jobs": jobs, "applied_item_count": len(applied),
              "aggregate": {arm: summarize([r for r in jobs if r["arm"] == arm])
                            for arm in ("sequential", "direct")},
              "required_coverage_and_topology": "Unchanged; no prediction, Gold identity, relation triples, or structural graph was edited.",
              "limitations": "User decisions cover disputed groups, not independent full human annotation. Admission and metadata failures are not automatically mathematical falsity."}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "adjudicated_metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
    print("Applied", len(applied), "explicit item decisions")


if __name__ == "__main__":
    main()
