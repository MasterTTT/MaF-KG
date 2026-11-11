#!/usr/bin/env python
"""S3 §6.4: audit the KST probe and record its withdrawal from the main evidence.

Two things are established here from artifacts only:

  * a definite scoring defect: one probe key carries a textbook formula-number fragment, so a
    correct model answer was scored as a miss;
  * the corrected arithmetic: the reported 0.225 is an edge-weighted single-end score, not a joint
    correctness rate, and macro and micro PSR must be reported separately.

No model is called.  The three existing PSR reports are not modified.

Usage:
    python analysis/final_closeout_s3_kst_audit.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KST = REPO / "evaluation/paper_closeout_2026-10-10/kst_probe"
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits"
PREPARE = REPO / "analysis/kst_probe_prepare.py"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recompute(order_name: str, probes: dict, report: dict) -> dict:
    orders = probes["orders"][order_name]
    probe_by_node = {p["node_id"]: p for p in probes["probes"]}
    scored = report["probes"]

    edges = []
    for target_node, prereq_nodes in orders.items():
        target_probe = probe_by_node.get(target_node)
        if not target_probe:
            continue
        for prereq_node in prereq_nodes:
            prereq_probe = probe_by_node.get(prereq_node)
            if not prereq_probe:
                continue
            for repeat in ("r1", "r2"):
                target_data = scored.get(target_probe["probe_id"], {}).get("repeats", {}).get(repeat)
                prereq_data = scored.get(prereq_probe["probe_id"], {}).get("repeats", {}).get(repeat)
                if not target_data or not prereq_data:
                    continue
                edges.append(
                    {
                        "target": target_probe["name"],
                        "prerequisite": prereq_probe["name"],
                        "repeat": repeat,
                        "target_all_keys": bool(target_data.get("all_keys")),
                        "prerequisite_all_keys": bool(prereq_data.get("all_keys")),
                        "target_hit_ratio": (
                            target_data.get("hit_count", 0) / target_data.get("key_count", 1)
                            if target_data.get("key_count")
                            else 0.0
                        ),
                        "prerequisite_hit_ratio": (
                            prereq_data.get("hit_count", 0) / prereq_data.get("key_count", 1)
                            if prereq_data.get("key_count")
                            else 0.0
                        ),
                    }
                )

    total = len(edges)
    target_ok = [e for e in edges if e["target_all_keys"]]
    prereq_ok = [e for e in edges if e["prerequisite_all_keys"]]
    both_ok = [e for e in edges if e["target_all_keys"] and e["prerequisite_all_keys"]]
    macro_denominator = len(target_ok)
    macro = (
        sum(e["prerequisite_hit_ratio"] for e in target_ok) / macro_denominator
        if macro_denominator
        else None
    )
    # macro over unique targets instead of edge-repeats, because the two aggregations differ and the
    # earlier report used the target-level one.
    per_target: dict[str, list[float]] = {}
    for edge in target_ok:
        per_target.setdefault(edge["target"], []).append(edge["prerequisite_hit_ratio"])
    macro_by_target = (
        sum(sum(values) / len(values) for values in per_target.values()) / len(per_target)
        if per_target
        else None
    )
    # micro uses counts, so recompute from the raw hit/key counts
    micro_hits = 0
    micro_keys = 0
    for edge in target_ok:
        prereq_probe = next(
            p for p in probes["probes"] if p["name"] == edge["prerequisite"]
        )
        data = scored[prereq_probe["probe_id"]]["repeats"][edge["repeat"]]
        micro_hits += data.get("hit_count", 0)
        micro_keys += data.get("key_count", 0)
    return {
        "order": order_name,
        "edges_scored": total,
        "unique_targets": len({e["target"] for e in edges}),
        "unique_targets_with_correct_target": len(per_target),
        "target_all_keys": len(target_ok),
        "prerequisite_all_keys": len(prereq_ok),
        "both_all_keys": len(both_ok),
        "joint_rate_over_all_edges": (len(both_ok) / total) if total else None,
        "psr_macro_over_edge_repeats": macro,
        "psr_macro_over_unique_targets": macro_by_target,
        "psr_micro": (micro_hits / micro_keys) if micro_keys else None,
        "psr_micro_counts": {"hits": micro_hits, "keys": micro_keys},
        "method": (
            "PSR is computed only over edges whose target is fully correct (KST NB1): "
            "macro = mean of the prerequisite hit ratio over those edges; micro = sum of "
            "prerequisite key hits divided by sum of prerequisite keys over the same edges. "
            "Both repeats are separate rows, so the denominator counts edge-repeats; the "
            "target-level macro is reported next to it because the earlier report used that unit."
        ),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    probes = read_json(KST / "probes.json")
    sharp = read_json(KST / "psr_report_sharp.json")
    full = read_json(KST / "psr_report_full.json")

    # 1. the definite scoring defect
    k03 = sharp["probes"].get("K03")
    response = read_json(KST / "responses_sharp/r1_K03.json")
    response_content = response["choices"][0]["message"]["content"]
    finish_reason = response["choices"][0].get("finish_reason")

    # 2. corrected arithmetic for the sharp variant, all three orders
    recomputed = {name: recompute(name, probes, sharp) for name in probes["orders"]}

    # 3. the earlier interpretation, for the record
    earlier = {
        name: {
            "edges": full["orders"][name]["edges"],
            "mean_target_score": full["orders"][name]["mean_target_score"],
            "mean_prerequisite_score": full["orders"][name]["mean_prerequisite_score"],
            "psr_cut_1.0": full["orders"][name].get("psr_cut_1.0"),
            "psr_cut_0.5": full["orders"][name].get("psr_cut_0.5"),
        }
        for name in full["orders"]
    }

    payload = {
        "schema": "dsh.final_closeout.s3_kst_audit.v1",
        "verdict": "withdrawn_from_main_evidence",
        "no_new_model_calls": True,
        "f2_reference_source": {
            "confirmed": True,
            "code_evidence": {
                "file": PREPARE.relative_to(REPO).as_posix(),
                "function": "orders(reference, nodes)",
                "lines": "133-165",
                "detail": (
                    "The 'extracted' order is built from reference['relations'] and "
                    "reference['cross_section_relations'] filtered by PREREQ_TYPES = "
                    "('uses', 'derived_from', 'defined_in_terms_of', 'special_case_of'). It is the "
                    "frozen reference graph, not the MaF output and not a predicted KU dependency "
                    "graph; and four different relation types are all read as teaching prerequisites."
                ),
            },
            "consequence": (
                "The probe cannot be described as behavioural validity evidence for the prerequisite "
                "graph this system predicts. The edge sample also contains both 'Lagrange depends on "
                "Rolle' and 'Rolle depends on Lagrange', which follows from reading special_case_of "
                "as a prerequisite."
            ),
            "edge_sample_evidence": sharp["orders"]["extracted"].get("edge_sample", [])[:6],
        },
        "f6_scoring_defect": {
            "probe_id": "K03",
            "probe_name": k03["name"] if k03 else None,
            "scoring_key_verbatim": (k03["keys"][0] if k03 and k03.get("keys") else None),
            "response_path": "evaluation/paper_closeout_2026-10-10/kst_probe/responses_sharp/r1_K03.json",
            "response_content_verbatim": response_content,
            "finish_reason": finish_reason,
            "defect": (
                "The key keeps the textbook formula-number fragment '（(5-2' from the frozen "
                "`meaning`, and the canonicalisation does not unify the Unicode subscript. The model "
                "returned the correct formula and was scored false."
            ),
            "judged_false_in_report": bool(
                k03 and k03.get("repeats", {}).get("r1", {}).get("hits")
            ),
        },
        "recomputed_sharp": recomputed,
        "earlier_interpretation_for_the_record": earlier,
        "discrepancies_with_earlier_report": [
            "0.225 is an edge-weighted single-end score, not a joint correctness rate; the joint "
            "rate is 2/40 edge-repeats.",
            "macro PSR and micro PSR must be reported separately; under this recomputation they "
            "coincide at 0.2222 because every edge whose target is correct has exactly one "
            "prerequisite key.",
            "The earlier macro 0.2857 = 2/7 aggregates over unique targets, while 0.2222 = 2/9 "
            "aggregates over edge-repeats. Both are recorded; the unit must be stated whenever the "
            "number is quoted.",
            "The three orders have different target sets and degree distributions, so their mean "
            "scores are not directly comparable.",
            "A correct differential expression was scored as a miss because the key carried a "
            "formula-number fragment.",
        ],
        "corrections_to_make_in_reports": [
            "Remove KST from the system-validity main evidence; keep it as a method-limitation and "
            "failure-diagnosis record.",
            "Replace any 'no violations detected, therefore self-consistent' or 'supports our "
            "extracted edges' statement with the corrected reading.",
            "Report the joint correctness rate together with macro and micro PSR, or report none.",
        ],
        "what_may_still_be_said": [
            "The probe attempt, its scoring rules and their three revisions are documented.",
            "Under the current keys the two ends are jointly correct on a very small number of "
            "edge-repeats; that number describes the keys, not the system.",
        ],
        "what_may_not_be_said": [
            "That the system's prerequisite graph was validated behaviourally.",
            "That the corrected arithmetic demonstrates teaching effectiveness.",
            "That the probe found no violations, therefore the extracted order is self-consistent.",
        ],
        "artifacts": [
            {"path": (KST / name).relative_to(REPO).as_posix(), "sha256": sha256_file(KST / name)}
            for name in ("probes.json", "psr_report_sharp.json", "psr_report_full.json")
        ],
    }
    out = OUT_DIR / "kst_probe_audit.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {out.relative_to(REPO).as_posix()}")
    print(f"  K03 key = {payload['f6_scoring_defect']['scoring_key_verbatim']!r}")
    print(f"  r1_K03 answer = {response_content!r} (finish_reason={finish_reason})")
    for name, block in recomputed.items():
        print(f"  {name:16s} edges={block['edges_scored']:3d} target_ok={block['target_all_keys']:3d} "
              f"prereq_ok={block['prerequisite_all_keys']:3d} both={block['both_all_keys']:3d} "
              f"macro_edges={block['psr_macro_over_edge_repeats']:.4f} "
              f"macro_targets={block['psr_macro_over_unique_targets']} "
              f"micro={block['psr_micro']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
