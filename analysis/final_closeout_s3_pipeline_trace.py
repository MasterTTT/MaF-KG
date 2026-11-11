#!/usr/bin/env python
"""S3 §6.1: build the real prerequisite pipeline trace.

Separates what actually ran for the legacy 27-pair targets from the single-stage proposal used on the
four new targets, and records every stage with a path and a hash.  A stage whose evidence is absent
is recorded as missing rather than reconstructed.

Read-only.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits/.

Usage:
    python analysis/final_closeout_s3_pipeline_trace.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02"
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits"
CONFIRM = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_of(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, list):
        return {"container": "list", "items": len(data)}
    if isinstance(data, dict):
        counts = {"container": "dict", "keys": sorted(data.keys())[:12]}
        for key in ("candidates", "verdicts", "endpoints", "reference", "proposals"):
            value = data.get(key)
            if isinstance(value, list):
                counts[key] = len(value)
        return counts
    return None


def artifact(rel: str, role: str) -> dict:
    path = REPO / rel
    if not path.is_file():
        return {"role": role, "path": rel, "status": "missing"}
    record = {
        "role": role,
        "path": rel,
        "status": "present",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    counts = count_of(path)
    if counts:
        record["counts"] = counts
    return record


STAGES = [
    {
        "stage_id": "l1_projection_baseline",
        "description": (
            "Project the existing L1 graph onto candidate prerequisite pairs for the frozen targets; "
            "no semantic completion."
        ),
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline_candidates.json", "candidates"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline/predictions.json", "verdicts"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline/batch_1.json", "request_packet"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline/payload_manifest.json", "payload_manifest"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline_score_input.json", "score_input"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline_scores.json", "scores"),
        ],
    },
    {
        "stage_id": "semantic_completion",
        "description": (
            "Ask the model to propose additional, earlier candidate prerequisites from the real KU "
            "descriptions, under a source-member gate."
        ),
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/proposal_input.json", "input"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/proposal_system.txt", "system_prompt"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/proposal_validation.json", "validation"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/semantic_candidates.json", "candidates"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/semantic_predictions.json", "verdicts"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/new_candidates/batch_1.json", "request_packet"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/new_candidates/payload_manifest.json", "payload_manifest"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/semantic_score_input.json", "score_input"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/semantic_scores.json", "scores"),
        ],
    },
    {
        "stage_id": "candidate_merge",
        "description": "Merge the projected and the semantically completed candidates into one pool.",
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/all_candidates.json", "merged_pool"),
        ],
    },
    {
        "stage_id": "independent_review",
        "description": (
            "Review the frozen pairs with the frozen review system prompt, independently of the "
            "candidate stage; this is the round the legacy reference was checked against."
        ),
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/batch_1.json", "packet_1"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/batch_2.json", "packet_2"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/batch_3.json", "packet_3"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/batch_4.json", "packet_4"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/predictions.json", "verdicts"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/score_input.json", "score_input"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs/scores.json", "scores"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/enhanced_system.txt", "enhanced_system_prompt"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/protocol.md", "protocol"),
        ],
    },
    {
        "stage_id": "reviewed_assembly",
        "description": "Assemble the reviewed KU graph that later queries run against.",
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/reviewed_assembly01/manifest.json", "manifest"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/reviewed_assembly01/ku.json", "ku"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/reviewed_assembly01/graph.json", "graph"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/reviewed_assembly01/catalog.json", "catalog"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/reviewed_assembly01/center_corrections.json", "center_corrections"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/reviewed_assembly01/query_config.json", "query_config"),
        ],
    },
    {
        "stage_id": "frozen_reference_and_inputs",
        "description": "The frozen inputs the run was judged against.",
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/reference.json", "reference"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json", "catalog"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/snapshot/review_system.txt", "review_system"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/snapshot/protocol.md", "protocol"),
        ],
    },
    {
        "stage_id": "ku_construction_inputs",
        "description": "Per-section KU plans and units used to build the catalogue.",
        "artifacts": [
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/units/ch1_s3.json", "units"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/units/ch5_s1.json", "units"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/units/ch5_s2.json", "units"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/units/ch5_s4.json", "units"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/units/ch7_s3.json", "units"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/plans/ch1_s3.json", "plan"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/plans/ch5_s1.json", "plan"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/plans/ch5_s2.json", "plan"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/plans/ch5_s4.json", "plan"),
            ("evaluation/prerequisite_new_targets_2026-10-07/run02/plans/ch7_s3.json", "plan"),
        ],
    },
]


def native_call_inventory() -> dict:
    requests = sorted((RUN / "native_requests").glob("*.json"))
    responses = sorted((RUN / "native_responses").glob("*.json"))
    transports = sorted((RUN / "native_transport").glob("*.json"))
    errors = sorted((RUN / "native_errors").glob("*.json"))
    calls = []
    for path in requests:
        record = {"request": path.name, "sha256": sha256_file(path)}
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            inner = body.get("body", body)
            record["model"] = inner.get("model")
            record["max_tokens"] = inner.get("max_tokens")
            record["temperature_in_body"] = "temperature" in inner
            messages = inner.get("messages")
            if isinstance(messages, list) and messages:
                record["system_prompt_sha256"] = hashlib.sha256(
                    str(messages[0].get("content", "")).encode("utf-8")
                ).hexdigest()
        except Exception as exc:  # noqa: BLE001
            record["unreadable"] = f"{type(exc).__name__}: {exc}"
        response = RUN / "native_responses" / path.name
        if response.is_file():
            record["response"] = response.name
            record["response_sha256"] = sha256_file(response)
        calls.append(record)
    return {
        "request_count": len(requests),
        "response_count": len(responses),
        "transport_count": len(transports),
        "error_count": len(errors),
        "errors": [p.name for p in errors],
        "calls": calls,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stages = []
    for spec in STAGES:
        artifacts = [artifact(rel, role) for rel, role in spec["artifacts"]]
        missing = [a["path"] for a in artifacts if a["status"] == "missing"]
        stages.append(
            {
                "stage_id": spec["stage_id"],
                "description": spec["description"],
                "status": "missing" if len(missing) == len(artifacts) else ("partial" if missing else "present"),
                "artifacts": artifacts,
                "missing_evidence": missing,
            }
        )

    new_targets = {
        "accurate_name": "目录输入的单阶段先修提议及角色判定",
        "is_full_pipeline": False,
        "targets": 4,
        "pairs": 28,
        "evidence": [
            "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/proposals/target_01.json",
            "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/proposal_requests/target_01.json",
            "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/proposal_responses/target_01.json",
            "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/end_to_end.json",
        ],
        "why_not_comparable": (
            "The proposal stage sees the catalogue name and short description of each target and one "
            "model call per target; it does not run the L1 projection, the semantic candidate "
            "completion or the independent reviewer of the legacy pipeline. Its numbers therefore "
            "describe a single-stage proposer under a fixed pool review, not an end-to-end pipeline."
        ),
    }

    proposals = []
    for path in sorted((CONFIRM / "proposals").glob("target_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        proposals.append(
            {
                "target_id": data.get("target_id"),
                "target_name": data.get("target_name"),
                "proposed_total": len(data.get("proposals", [])),
                "proposed_required": sum(1 for p in data.get("proposals", []) if p.get("role") == "required"),
                "path": path.relative_to(REPO).as_posix(),
                "sha256": sha256_file(path),
            }
        )

    payload = {
        "schema": "dsh.final_closeout.s3_real_pipeline_trace.v1",
        "no_new_model_calls": True,
        "pipeline_name": "legacy real prerequisite pipeline (run02, legacy 27-pair targets)",
        "run_root": RUN.relative_to(REPO).as_posix(),
        "stage_order_basis": (
            "Stage order is taken from the driver scripts named in prediction01/software/ "
            "(run_ku_candidate_retrieval_experiment.py -> run_semantic_ku_candidate_experiment.py -> "
            "run_new_target_prediction.py) and from the artifact each stage wrote; no stage was "
            "reconstructed from a later artifact."
        ),
        "stages": stages,
        "native_call_inventory": native_call_inventory(),
        "new_four_target_status": new_targets,
        "new_four_target_proposals": proposals,
        "unlabeled_or_unknown_handling": (
            "The proposer emitted 14 proposals across the four targets; two of them are not covered "
            "by the 28-pair reference. A predicted edge that is absent from the reference is not a "
            "confirmed negative and is reported as unknown, never as a false positive."
        ),
        "what_this_run_can_support": [
            "A limited real-pipeline reading on the legacy 27-pair targets: candidate projection "
            "recall, semantic completion recall, and an independent review round on the same pairs.",
            "The distinction between candidate-pool conditioning and full-graph recall.",
        ],
        "what_this_run_cannot_support": [
            "Any end-to-end claim for the four new targets.",
            "Any claim about whole-book prerequisite coverage.",
            "Any claim that the new 28-pair numbers came from the deployed pipeline.",
        ],
    }
    out = OUT_DIR / "real_pipeline_trace.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {out.relative_to(REPO).as_posix()}")
    for stage in stages:
        present = sum(1 for a in stage["artifacts"] if a["status"] == "present")
        print(f"  {stage['stage_id']:28s} {stage['status']:8s} {present}/{len(stage['artifacts'])} artifacts")
    inv = payload["native_call_inventory"]
    print(f"  native calls: requests={inv['request_count']} responses={inv['response_count']} "
          f"errors={inv['error_count']}")
    print(f"  new-target proposals: {len(proposals)} targets, "
          f"{sum(p['proposed_total'] for p in proposals)} proposals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
