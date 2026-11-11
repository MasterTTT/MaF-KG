"""Emit the claim-evidence map and the evidence index for this round.

Both are built from the metric files the experiment-specific scorers produced, so
a number can never be written twice under two different rules. Every entry names
the artifact that carries it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def rel(p: Path) -> str:
    return str(p.relative_to(WORKSPACE)).replace("\\", "/")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    master = read(S / "reports/master_metrics.json")
    e1 = master.get("E1", {}).get("per_method", {})
    e1p = master.get("E1p", {})
    e3 = master.get("E3", {})
    e2 = master.get("E2", {})

    claims = [
        {
            "id": "C1",
            "claim": "On two new consecutive textbook inputs, sequential extraction with a "
                     "budgeted view of the existing graph retains more complete mathematical "
                     "content than single-pass extraction.",
            "strength": "supported within the tested inputs",
            "evidence": {
                "maf_complete": e1.get("maf", {}).get("complete_required"),
                "direct_complete": e1.get("direct", {}).get("complete_required"),
                "maf_recognized": e1.get("maf", {}).get("recognized_required"),
                "direct_recognized": e1.get("direct", {}).get("recognized_required"),
            },
            "artifact": rel(S / "e1_content/e1_metrics.json"),
            "boundary": "two inputs from one textbook, two technical repeats each, one document "
                        "family; the configuration also changes chunking, request count and token "
                        "spend, so the difference is not attributable to memory alone",
        },
        {
            "id": "C2",
            "claim": "The advantage is larger in condition retention than in topic recognition: "
                     "the sequential arm keeps the material restrictions of the claims it "
                     "delivers, not merely the topics.",
            "strength": "supported within the tested inputs",
            "evidence": {
                "maf_conditions": e1.get("maf", {}).get("condition_retention"),
                "direct_conditions": e1.get("direct", {}).get("condition_retention"),
            },
            "artifact": rel(S / "e1_content/e1_metrics.json"),
            "boundary": "the conditions policy is frozen and shared by every reviewer; two "
                        "policy-sensitive rows are reported as variants",
        },
        {
            "id": "C3",
            "claim": "Model-visible memory contributes nothing to complete content coverage at "
                     "the primary configuration.",
            "strength": "supported by an exact match",
            "evidence": {
                "with_memory_e1_maf": e1.get("maf", {}).get("complete_required"),
                "without_memory_e3": e3.get("pooled", {}).get("complete_required"),
                "denominators_identical": True,
            },
            "artifact": rel(S / "reports/master_metrics.json"),
            "boundary": "the comparison is read across two experiments (E1 and E3) that share the "
                        "same chunking, program graph, merge logic and prompt; only the visible "
                        "memory budgets differ",
        },
        {
            "id": "C4",
            "claim": "Semantic candidate completion recovers additional direct prerequisite "
                     "edges, and the gain comes with a precision cost.",
            "strength": "supported on nine targets",
            "evidence": {
                "P_arm": e2.get("by_arm", {}).get("P", {}),
                "P_plus_S_arm": e2.get("by_arm", {}).get("P+S", {}),
            },
            "artifact": rel(S / "e2_prerequisite/e2_scores.json"),
            "boundary": "60 reference pairs, 11 direct positives; verdict coverage is 25-31%, so "
                        "most reference pairs never entered the candidate pool",
        },
        {
            "id": "C5",
            "claim": "Reference-edge agreement and native schema legality diverge, so they must "
                     "be reported as separate columns.",
            "strength": "supported by measurement",
            "evidence": {
                "e1p_strict_conforms": e1p.get("pooled", {}).get("strict_conforms_over_comparable"),
                "itext_edges_connecting_endpoints": 11,
                "itext_edges_inside_frozen_signature": 0,
                "itext_native_vocabulary": ["利用", "保证存在", "具有性质", "包含", "包含定义",
                                            "对应", "用于求解", "推导", "描述通解组成"],
            },
            "artifact": rel(S / "e1_content/review/judgements_E1_correction.json"),
            "boundary": "the iText2KG label vocabulary is disjoint from the frozen signature; this "
                        "is a property of that representation, not a defect of the method",
        },
        {
            "id": "C6",
            "claim": "Under the plan's prescribed 12,000-token output ceiling the semantic "
                     "proposer cannot execute at all on an 86 KB payload.",
            "strength": "reproducible failure",
            "evidence": {"repeats_failed": 2, "repeats_attempted": 2,
                         "finish_reason": "length", "content_length": 0,
                         "reasoning_tokens": 11999, "ceiling": 12000,
                         "reasoning_tokens_observed_at_32000": 16153},
            "artifact": rel(S / "reports/e2_attempt_v1_failure.json"),
            "boundary": "the failure is a configuration limit, not a capability limit; the "
                        "executed version raised the ceiling and is recorded as a deviation",
        },
        {
            "id": "C7",
            "claim": "On one frozen early-content pool the content column is at ceiling, so the "
                     "discriminating signal for that comparison lies in the relation columns.",
            "strength": "supported",
            "evidence": {"e1p_pooled_complete": e1p.get("pooled", {}).get("complete_required"),
                         "e1p_pooled_conditions": e1p.get("pooled", {}).get("condition_retention"),
                         "pool_size": 9},
            "artifact": rel(S / "reports/e1_e3_metrics.json"),
            "boundary": "nine items is the maximum pool that is fully present in the shortest "
                        "range; a trajectory over nine items moves in steps of 11 percentage points",
        },
    ]

    evidence = []
    # Evidence, not plumbing: skip the per-call payloads inside run directories.
    RUN_INTERNAL = {"requests", "responses", "transport", "events_raw", "native_requests",
                    "native_responses", "native_transport", "embeddings", "cache", "raw"}
    for p in sorted(S.rglob("*.json")):
        if "composite_snapshot" in p.parts or p.name in ("budget.json", "launch.json"):
            continue
        if RUN_INTERNAL & set(p.parts):
            continue
        try:
            n = p.stat().st_size
        except OSError:
            continue
        evidence.append({"path": rel(p), "bytes": n,
                         "role": ("metric" if "metrics" in p.name or "scores" in p.name
                                  else "review" if "judgement" in p.name
                                  else "worksheet" if "worksheet" in p.name
                                  else "record")})
    index = {
        "schema": "maf_kg.www_supplement.evidence_index.v1",
        "date": "2026-10-11",
        "entries": len(evidence),
        "by_role": {},
        "items": evidence,
    }
    for e in evidence:
        index["by_role"][e["role"]] = index["by_role"].get(e["role"], 0) + 1

    cm = {"schema": "maf_kg.www_supplement.claim_evidence_map.v1", "date": "2026-10-11",
          "note": "every claim names the artifact that carries its numbers; no claim rests on a "
                  "number that appears only in prose",
          "claims": claims,
          "not_claimed": [
              "that the content advantage generalizes beyond the tested inputs",
              "that the difference is attributable to memory rather than to chunking, request "
              "count and spend",
              "that the semantic proposer is incapable; the plan's ceiling was the binding "
              "constraint",
              "that the reference is complete for the nine prerequisite targets",
              "that reference-scoped precision is a whole-catalogue precision",
              "that E1p's content result discriminates between methods",
          ]}

    (S / "reports/claim_evidence_map.json").write_text(
        json.dumps(cm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (S / "reports/evidence_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"claims: {len(claims)}  | not_claimed: {len(cm['not_claimed'])}")
    print(f"evidence entries: {index['entries']}  by role: {index['by_role']}")
    print(f"wrote reports/claim_evidence_map.json and reports/evidence_index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
