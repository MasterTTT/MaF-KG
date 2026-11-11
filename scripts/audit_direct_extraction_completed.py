"""Supplementary mechanical audit; does not change frozen semantic scoring."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from run_direct_extraction_baseline import audit_job, audit_prepared, read
from run_memory_ablation import digest, write_json


def audit(output: Path) -> dict:
    manifest = read(output / "manifest.json")
    audit_prepared(manifest)
    expected = {(s["id"], r, a) for s in manifest["sections"]
                for r in (1, 2) for a in ("sequential", "direct")}
    observed = [(j["section"], j["repeat"], j["name"]) for j in manifest["jobs"]]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("Pair coverage differs from the fixed protocol")
    if any(j["status"] != "completed" for j in manifest["jobs"]):
        raise ValueError("Incomplete experiment: do not compare arm totals")
    sections = []
    ledger = read(output / "evaluation_snapshot/evaluation/direct_baseline_2026-10-03/source_condition_ledger_v1.json")
    for section in manifest["sections"]:
        plan = Path(section["plan_dir"])
        reachability = read(plan / "reachability.json")
        required = [r for r in reachability if r["requirement"] == "M"]
        if any(r["input_status"] != "full" for r in required):
            raise ValueError("This completed-report audit requires all M source spans reachable")
        evidence_map = read(plan / "evidence_map.json")
        original_visible = {n + evidence_map["source_line_offset"]
                            for n in evidence_map["selected_local_lines"]}
        checklist_rows = [n for n in ledger["nodes"] if n["gold_id"] in {r["gold_id"] for r in required}]
        for node in checklist_rows:
            checklist_lines = set(node["source_lines"])
            for check in node["checks"]:
                checklist_lines.update(check["source_lines"])
            if not checklist_lines <= original_visible:
                raise ValueError("Source-condition checklist contains invisible lines")
        templates = read(plan / "templates.json")
        candidates = {}
        for arm in ("sequential", "direct"):
            pool = {}
            for template in templates[arm]:
                for c in template["source_latex_candidates"]:
                    cid = c["candidate_id"]
                    if cid in pool and pool[cid] != c:
                        raise ValueError("A repeated candidate ID changes content")
                    pool[cid] = c
            candidates[arm] = pool
        if candidates["sequential"] != candidates["direct"]:
            raise ValueError("Formula candidate union differs")
        sections.append({"section": section["id"], "required_full": len(required),
                         "required_partial": 0, "required_unreachable": 0,
                         "core_line_count": section["core_line_count"],
                         "fully_visible_condition_checklists": len(checklist_rows),
                         "candidate_union_count": len(candidates["direct"]),
                         "reachability": reachability})
    jobs = []
    for job in manifest["jobs"]:
        checked = audit_job(manifest, job)
        directory = Path(job["directory"])
        attempts = read(directory / "api_requests.json")
        raw = list((directory / "raw").glob("*.json"))
        if any(a["status"] != "success" or a["finish_reason"] != "stop" for a in attempts):
            raise ValueError("Failed/truncated attempt: report it explicitly before comparison")
        if len(raw) != len(attempts) or len(attempts) != checked["api_summary"]["request_count"]:
            raise ValueError("Transport archive and request telemetry counts differ")
        review = read(directory / "review.json")
        accepted_statuses = {"assistant explicit source-based review",
                             "explicit review against frozen source and reference"}
        if review["status"] not in accepted_statuses:
            raise ValueError("Draft or unknown review status cannot enter the report")
        graph = read(directory / "graph.json")
        decisions = [d for c in graph["diagnostics"]["chunk_summaries"] for d in c["decisions"]]
        created = Counter(d["node_id"] for d in decisions
                          if d["action"] == "create" and d["status"] == "accepted")
        response_creates = sum(a["action"] == "create"
                               for p in (directory / "responses").glob("*.json")
                               for a in read(p)["node_actions"])
        jobs.append({"section": job["section"], "repeat": job["repeat"], "arm": job["name"],
                     "graph_sha256": checked["graph_sha256"], "review_sha256": digest(directory / "review.json"),
                     "attempts": len(attempts), "failed_attempts": 0, "truncated_attempts": 0,
                     "raw_transport_files": len(raw), "response_create_actions": response_creates,
                     "accepted_create_actions": sum(created.values()),
                     "distinct_accepted_create_ids": len(created),
                     "repeated_create_id_extra": sum(n - 1 for n in created.values()),
                     "api_summary": checked["api_summary"], "elapsed_seconds": job["elapsed_seconds"]})
    result = {"status": "complete paired run, all M reachable, terminal explicit reviews",
              "scope": "Mechanical audit only; repeated-create IDs are not semantic duplicate judgments.",
              "scorer_limitations": ["Frozen scorer uses identity-mapped relation endpoints, not content-carrier endpoints.",
                                     "Condition completeness counts whole reference units, not individual conditions.",
                                     "No incomplete-job or unreachable-M case occurs in this run."],
              "sections": sections, "jobs": jobs}
    write_json(output / "completion_audit.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory.resolve())
    print(f"Audited {len(result['jobs'])} completed jobs")
