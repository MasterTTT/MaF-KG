"""Score frozen KU membership constraints plus explicit target/unit reviews."""
from __future__ import annotations

import argparse
import itertools
import shutil
from pathlib import Path

from run_ku_boundary_baseline import audit, read, write_json
from run_memory_ablation import digest


def freeze(output: Path) -> None:
    destination = output / "evaluation_snapshot"
    destination.mkdir(exist_ok=False)
    shutil.copy2(__file__, destination / "score_ku_boundary_baseline.py")
    write_json(destination / "manifest.json", {"scorer_sha256": digest(Path(__file__)),
               "status": "Frozen before any L2 prediction; target/unit reviews required."})


def score(output: Path) -> dict:
    manifest = read(output / "manifest.json")
    audit(manifest)
    fixed = read(output / "evaluation_snapshot/manifest.json")["scorer_sha256"]
    if digest(Path(__file__)) != fixed or digest(output / "evaluation_snapshot/score_ku_boundary_baseline.py") != fixed:
        raise ValueError("Scoring software changed")
    if any(j["status"] != "completed" for j in manifest["jobs"]):
        raise ValueError("Incomplete pair; report failure instead of comparison")
    reference = read(Path(manifest["reference"]))
    rows = []
    for job in manifest["jobs"]:
        directory = Path(job["directory"])
        units = read(directory / "units.json")["units"]
        graph = read(next(Path(s["graph"]) for s in manifest["sections"] if s["id"] == job["section"]))
        units_by_id = {u["id"]: u for u in units}
        owner = {}
        for unit in units:
            if not unit["centers_on"] or len(unit["centers_on"]) > 3:
                raise ValueError("Missing or excessive centers")
            for node_id in unit["centers_on"] + unit["contains"]:
                if node_id in owner:
                    raise ValueError("Repeated main membership")
                owner[node_id] = unit["id"]
        if set(owner) != {n["id"] for n in graph["nodes"]}:
            raise ValueError("Input membership coverage differs")
        alignment = read(Path(manifest["snapshot"]) / "alignments" / f"{job['section']}.json")
        mapping = {r["gold_id"]: r["identity_id"] for r in alignment["gold_reviews"] if r["identity_id"]}
        targets = {t["id"]: t for t in reference["knowledge_units"] if t["section"] == job["section"]}
        mapped_core = {tid: [mapping[g] for g in t["core_members"] if g in mapping]
                       for tid, t in targets.items()}
        positive = set(tuple(sorted(pair)) for ids in mapped_core.values() for pair in itertools.combinations(ids, 2))
        negative = set()
        for left, right in reference["forbidden_ku_merges"]:
            if left in mapped_core and right in mapped_core:
                negative.update(tuple(sorted(pair)) for pair in itertools.product(mapped_core[left], mapped_core[right]))
        review_path = directory / "review.json"
        review = read(review_path)
        if (review["units_sha256"] != digest(directory / "units.json")
                or review["reference_sha256"] != digest(Path(manifest["reference"]))
                or review["status"] != "assistant explicit source-based review" or not review["reviewer"].strip()):
            raise ValueError("Review binding or status invalid")
        judgments = review["target_reviews"]
        if len(judgments) != len(targets) or {r["target_id"] for r in judgments} != set(targets):
            raise ValueError("Each reference target requires one explicit review")
        matched = []
        center_hits = center_total = complete_core = 0
        for row in judgments:
            if not row["reason"].strip():
                raise ValueError("Missing target reason")
            uid = row["unit_id"]
            if uid is not None:
                if uid not in units_by_id or uid in matched:
                    raise ValueError("Target identity must be one-to-one")
                matched.append(uid)
            target = targets[row["target_id"]]
            expected_centers = [mapping[g] for g in target["centers"] if g in mapping]
            center_total += len(expected_centers)
            center_hits += sum(n in units_by_id[uid]["centers_on"] for n in expected_centers) if uid else 0
            complete_core += bool(uid and all(owner[n] == uid for n in mapped_core[row["target_id"]])
                                  and mapped_core[row["target_id"]])
        unit_reviews = review["unit_reviews"]
        if len(unit_reviews) != len(units) or {r["unit_id"] for r in unit_reviews} != set(units_by_id):
            raise ValueError("Every predicted unit must be reviewed")
        verdicts = {"valid", "overmerged", "fragmented", "misattributed", "uncertain"}
        if any(r["verdict"] not in verdicts or not r["reason"].strip() for r in unit_reviews):
            raise ValueError("Invalid unit review")
        telemetry = read(directory / "api_requests.json")
        row = {"section": job["section"], "arm": job["arm"], "counts": {
            "nodes": len(owner), "units": len(units), "targets": len(targets), "target_identity_hits": len(matched),
            "targets_with_complete_core_members": complete_core, "center_hits": center_hits, "available_centers": center_total,
            "must_link_pairs": len(positive), "must_link_satisfied": sum(owner[a] == owner[b] for a, b in positive),
            "cannot_link_pairs": len(negative), "cannot_link_satisfied": sum(owner[a] != owner[b] for a, b in negative),
            "unavailable_core_members": sum(g not in mapping for t in targets.values() for g in t["core_members"]),
            **{f"unit_{v}": sum(r["verdict"] == v for r in unit_reviews) for v in verdicts}},
            "total_tokens": sum(a.get("usage", {}).get("total_tokens", 0) for a in telemetry),
            "attempts": len(telemetry), "failed_attempts": sum(a["status"] != "success" for a in telemetry),
            "response_models": sorted({a.get("response_model", "") for a in telemetry}),
            "review_sha256": digest(review_path), "elapsed_seconds": job["elapsed_seconds"]}
        write_json(directory / "reviewed_metrics.json", row)
        rows.append(row)
    result = {"status": "assistant preliminary review, one development pair per section; not human Gold accuracy",
              "scope": "Pair pools derive only from available Gold core members; support references are not forced into unique duplicate memberships.",
              "jobs": rows}
    write_json(output / "reviewed_metrics.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    if args.freeze:
        freeze(args.directory.resolve())
    else:
        result = score(args.directory.resolve())
        for row in result["jobs"]:
            print(row["section"], row["arm"], row["counts"])
