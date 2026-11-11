"""Score explicit assistant/human reviews, never automatic semantic matching."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from run_direct_extraction_baseline import read, audit_prepared, audit_job
from run_memory_ablation import digest, write_json
from score_cross_topic_memory_ablation import validate_decisions


def freeze_scoring(output: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    target = output / "evaluation_snapshot"
    target.mkdir(exist_ok=False)
    sources = [Path(__file__), root / "scripts/run_direct_extraction_baseline.py",
               root / "scripts/run_memory_ablation.py", root / "scripts/score_cross_topic_memory_ablation.py",
               root / "scripts/score_memory_ablation.py", root / "tests/test_score_direct_extraction_baseline.py",
               root / "evaluation/direct_baseline_2026-10-03/source_condition_ledger_v1.json"]
    artifacts = []
    for source in sources:
        destination = target / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        artifacts.append({"live": str(source), "snapshot": str(destination), "sha256": digest(destination)})
    write_json(target / "manifest.json", {"created_at": datetime.now(timezone.utc).isoformat(),
               "status": "Evaluation software/source checklist fixed before prediction review; generation already started.",
               "artifacts": artifacts})


def audit_scoring(output: Path) -> None:
    manifest = read(output / "evaluation_snapshot/manifest.json")
    for row in manifest["artifacts"]:
        if digest(Path(row["snapshot"])) != row["sha256"] or digest(Path(row["live"])) != row["sha256"]:
            raise ValueError("Evaluation software or source checklist differs from frozen review protocol")


def score_review(graph: dict, reference: dict, section_id: str, review: dict) -> dict:
    nodes = {node["id"]: node for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("Duplicate prediction ID")
    validate_decisions(review["node_reviews"], graph["nodes"], "node")
    validate_decisions(review["relation_reviews"], graph["relations"], "relation")
    expected = {node["id"]: node for node in reference["nodes"] if node["section"] == section_id}
    judged = review["gold_reviews"]
    if len(judged) != len(expected) or {row["gold_id"] for row in judged} != set(expected):
        raise ValueError("Each section reference needs exactly one explicit review")
    identities = {}
    records = []
    for row in judged:
        gold = expected[row["gold_id"]]
        identity = row["identity_id"]
        content_ids = row["content_ids"]
        if (not isinstance(content_ids, list) or len(content_ids) != len(set(content_ids))
                or any(i not in nodes for i in content_ids) or not row["reason"].strip()):
            raise ValueError("Content mapping or explicit reason is invalid")
        if identity is not None:
            if identity not in nodes or nodes[identity]["type"] != gold["type"] or identity not in content_ids:
                raise ValueError("Independent identity must match type and a content carrier")
            if identity in identities.values():
                raise ValueError("Independent identities must align one-to-one")
            identities[gold["id"]] = identity
        for key in ("description_complete", "conditions_complete"):
            if not isinstance(row[key], bool):
                raise ValueError("Completeness must be explicitly true/false")
            if row[key] and not content_ids:
                raise ValueError("An absent item cannot have complete content")
        records.append({**row, "requirement": gold["status"], "tier": gold["tier"], "type": gold["type"]})
    required = [r for r in records if r["requirement"] == "M"]
    relations = [r for r in reference["relations_provisional"] if r["start"] in expected and r["end"] in expected]
    relation_verdicts = {r["id"]: r["verdict"] for r in review["relation_reviews"]}
    triples = {(r["source_id"], r["type"], r["target_id"]) for r in graph["relations"]
               if relation_verdicts[r["id"]] == "valid"}
    relation_checks = []
    for relation in relations:
        source, target = identities.get(relation["start"]), identities.get(relation["end"])
        relation_checks.append({"gold_id": relation["id"], "endpoints_available": bool(source and target),
                                "hit": bool(source and target and (source, relation["type"], target) in triples)})
    duplicate_ids = set()
    duplicate_extra = 0
    for group in review["duplicate_groups"]:
        ids = group["ids"]
        if len(ids) < 2 or len(set(ids)) != len(ids) or any(i not in nodes for i in ids) or not group["reason"].strip():
            raise ValueError("Duplicate group requires known distinct IDs and a reason")
        if duplicate_ids & set(ids):
            raise ValueError("Duplicate groups cannot overlap")
        duplicate_ids.update(ids)
        duplicate_extra += len(ids) - 1
    false_merges = review["false_merges"]
    if any(r["id"] not in nodes or not r["reason"].strip() for r in false_merges):
        raise ValueError("False merge annotation is invalid")
    if len({r["id"] for r in false_merges}) != len(false_merges):
        raise ValueError("Duplicate false-merge annotation")
    incident = {i for r in graph["relations"] for i in (r["source_id"], r["target_id"])}
    counts = {
        "required_total": len(required), "identity_hits": sum(r["identity_id"] is not None for r in required),
        "content_hits": sum(bool(r["content_ids"]) for r in required),
        "complete_descriptions": sum(r["description_complete"] for r in required),
        "complete_condition_units": sum(r["conditions_complete"] for r in required),
        "provisional_relation_total": len(relations), "provisional_relation_hits": sum(r["hit"] for r in relation_checks),
        "provisional_relation_available_endpoints": sum(r["endpoints_available"] for r in relation_checks),
        "duplicate_extra_nodes": duplicate_extra, "false_merges": len(false_merges),
        "isolated_nodes": len(set(nodes) - incident), "nodes": len(nodes), "relations": len(graph["relations"]),
    }
    for name in ("node", "relation"):
        for verdict in ("valid", "modeling_issue", "uncertain"):
            counts[f"{name}_{verdict}"] = sum(r["verdict"] == verdict for r in review[f"{name}_reviews"])
    return {"counts": counts, "gold_reviews": records, "provisional_relation_checks": relation_checks}


def score(output: Path) -> dict:
    audit_scoring(output)
    manifest = read(output / "manifest.json")
    audit_prepared(manifest)
    reference = read(Path(manifest["reference"]))
    rows = []
    for job in manifest["jobs"]:
        if job["status"] != "completed":
            rows.append({"section": job["section"], "repeat": job["repeat"], "arm": job["name"],
                         "status": job["status"], "error": job.get("error")})
            continue
        audit = audit_job(manifest, job)
        directory = Path(job["directory"])
        review_path = directory / "review.json"
        review = read(review_path)
        if review["graph_sha256"] != digest(directory / "graph.json") or review["reference_sha256"] != digest(Path(manifest["reference"])):
            raise ValueError("Review does not bind to the current graph and reference")
        if not review["reviewer"].strip() or not review["status"].strip():
            raise ValueError("Reviewer/status must be explicit")
        metrics = score_review(read(directory / "graph.json"), reference, job["section"], review)
        row = {"section": job["section"], "repeat": job["repeat"], "arm": job["name"], "status": "scored",
               "reviewer": review["reviewer"], "review_status": review["status"],
               "review_sha256": digest(review_path), **metrics,
               "api_summary": audit["api_summary"], "elapsed_seconds": job["elapsed_seconds"]}
        write_json(directory / "reviewed_metrics.json", row)
        rows.append(row)
    aggregate = {}
    for arm in ("sequential", "direct"):
        matched = [r for r in rows if r["arm"] == arm and r["status"] == "scored"]
        counts = {key: sum(r["counts"][key] for r in matched) for key in matched[0]["counts"]} if matched else {}
        token_sum = sum(r["api_summary"]["usage_totals"]["total_tokens"] for r in matched)
        aggregate[arm] = {"scored_jobs": len(matched), "counts_sum": counts, "total_tokens_completed_jobs": token_sum,
                          "note": "Repeated sections are not independent Gold items; report each pair, not only sums."}
    result = {"status": "explicit assistant review unless a human reviewer is recorded; not automatic semantic scoring",
              "scoring_snapshot_manifest_sha256": digest(output / "evaluation_snapshot/manifest.json"),
              "reference_sha256": digest(Path(manifest["reference"])), "jobs": rows, "aggregate": aggregate,
              "metric_scope": "Finite reference pool; complete_condition_units is whole-node source-checklist completeness, not individual-condition recall or full-graph accuracy."}
    write_json(output / "reviewed_metrics.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    if args.freeze_only:
        freeze_scoring(args.directory.resolve())
    else:
        result = score(args.directory.resolve())
        print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
