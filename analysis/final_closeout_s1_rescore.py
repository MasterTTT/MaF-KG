#!/usr/bin/env python
"""S1 §4.4: rescore the prerequisite experiments against the adjudicated reference.

Two report columns are produced for every arm:

  frozen_scorer  -- the unchanged frozen scorer, kept for comparability with everything already
                    reported.  Its conditional subset silently drops model abstentions from the
                    recall denominator.
  main           -- the primary reading required by the closeout plan: every determinate reference
                    positive stays in the recall denominator (an abstention is a miss, not a
                    deletion), and a predicted edge that is not in the reference at all is reported
                    as ``unknown`` instead of being counted as a false positive.

No model is called.  Only existing responses are read.

Usage:
    python analysis/final_closeout_s1_rescore.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPS = REPO / "evaluation/prerequisite_candidate_retrieval_2026-10-07/run01/runtime_dependencies"
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_rescore"
ADJ_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication"

sys.path.insert(0, str(DEPS))
sys.stdout.reconfigure(encoding="utf-8")

from score_ku_prerequisite_evaluation import score_evaluation  # noqa: E402

OLD_FROZEN = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/diagnostics01/judge_all_pairs"
OLD_PREDICTION = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01"
CONFIRM = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation"
CONTEXT = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_context"

REFERENCE_VERSIONS = {
    "old27_v1": {
        "source": OLD_FROZEN / "score_input.json",
        "note": "Original 27-pair reference; unchanged by this round, scope-checked for the same conflict.",
    },
    "new28_v2": {
        "source": CONFIRM / "reference_v2.json",
        "note": "New 28-pair reference as frozen before prediction; the version the acceptance review disputes.",
    },
    "new28_v3": {
        "source": ADJ_DIR / "reference_v3.json",
        "note": "Adjudicated version built in S1; same pairs, corrected roles and directness.",
    },
    "new28_v3_n04_required": {
        "source": ADJ_DIR / "reference_v3_n04_required.json",
        "note": (
            "Sensitivity variant: identical to v3 except N04 stays required/direct, i.e. the target "
            "scope includes judging whether a given function is an antiderivative."
        ),
    },
}

ARMS = [
    {
        "arm_id": "old27.A",
        "reference_version": "old27_v1",
        "predictions": OLD_FROZEN / "predictions.json",
        "index_based": False,
        "note": "Reused frozen judgement round on KU + member descriptions.",
    },
    {
        "arm_id": "old27.B",
        "reference_version": "old27_v1",
        "predictions": CONTEXT / "armB/predictions.json",
        "index_based": True,
        "note": "Same 27 pairs with the real source text added.",
    },
    {
        "arm_id": "new28.ku",
        "reference_version": "new28_v2",
        "predictions": CONFIRM / "arm_ku/predictions.json",
        "index_based": True,
        "note": "Four new targets, catalogue entries only.",
    },
    {
        "arm_id": "new28.ctx",
        "reference_version": "new28_v2",
        "predictions": CONFIRM / "arm_ctx/predictions.json",
        "index_based": True,
        "note": "Same pairs with the real Card text added.",
    },
    {
        "arm_id": "real_pipeline.semantic",
        "reference_version": "old27_v1",
        "predictions": OLD_PREDICTION / "semantic_predictions.json",
        "index_based": False,
        "note": (
            "The real pipeline's semantic-completion candidates on the old 27 pairs; only 10 "
            "candidate pairs were proposed, so this is candidate recall, not full-graph coverage."
        ),
    },
    {
        "arm_id": "real_pipeline.baseline",
        "reference_version": "old27_v1",
        "predictions": OLD_PREDICTION / "baseline/predictions.json",
        "index_based": False,
        "note": "The L1-projection baseline candidates on the same 27 pairs, without semantic completion.",
    },
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def reference_pairs(version: str) -> tuple[list[dict], dict]:
    spec = REFERENCE_VERSIONS[version]
    path = spec["source"]
    if not path.is_file():
        return [], {"available": False, "path": path.relative_to(REPO).as_posix()}
    raw = read_json(path)
    if version == "old27_v1":
        return raw["reference"], {"available": True, "path": path.relative_to(REPO).as_posix()}
    return raw["pairs"], {"available": True, "path": path.relative_to(REPO).as_posix()}


def endpoints_for(version: str) -> list[dict]:
    if version == "old27_v1":
        return read_json(OLD_FROZEN / "score_input.json")["endpoints"]
    catalog = read_json(
        REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json"
    )
    return catalog["knowledge_units"]


def arm_verdicts(spec: dict, pairs: list[dict]) -> list[dict]:
    raw = read_json(spec["predictions"])
    rows = raw.get("verdicts", [])
    out = []
    for row in rows:
        if spec["index_based"]:
            pair = pairs[row["index"]]
            target_ku, prerequisite_ku = pair["target_ku"], pair["prerequisite_ku"]
        else:
            # The prediction already carries the edge in dependent -> prerequisite order.
            target_ku, prerequisite_ku = row["source_ku"], row["target_ku"]
        verdict = {
            "source_ku": target_ku,
            "target_ku": prerequisite_ku,
            "label": row["label"],
            "reason": row.get("reason", ""),
        }
        if row["label"] == "required":
            verdict["directness"] = row["directness"]
        out.append(verdict)
    return out


def main_metrics(pairs: list[dict], verdicts: list[dict]) -> dict:
    """Unconditional reading: abstentions stay in the denominator, unlabelled predictions are unknown."""
    gold = {}
    for pair in pairs:
        gold[(pair["prerequisite_ku"], pair["target_ku"])] = pair
    determinate = {k: v for k, v in gold.items() if v.get("role") != "uncertain"}

    predicted_required = set()
    predicted_direct = set()
    abstained = set()
    judged = set()
    for verdict in verdicts:
        edge = (verdict["target_ku"], verdict["source_ku"])
        label = verdict["label"]
        judged.add(edge)
        if label == "uncertain":
            abstained.add(edge)
        elif label == "required":
            predicted_required.add(edge)
            if verdict.get("directness") == "direct":
                predicted_direct.add(edge)

    gold_required = {k for k, v in determinate.items() if v["role"] == "required"}
    gold_direct = {k for k, v in determinate.items() if v.get("directness") == "direct"}
    gold_indirect = {k for k, v in determinate.items() if v.get("directness") == "indirect"}
    gold_support = {k for k, v in determinate.items() if v["role"] in {"principle_support", "proof_support"}}
    gold_negative = {k for k, v in determinate.items() if v["role"] == "related_or_none"}
    gold_uncertain = {k for k, v in gold.items() if v.get("role") == "uncertain"}

    def metrics(predicted: set, positive: set, universe: set) -> dict:
        tp = len(predicted & positive)
        fp = len(predicted & universe - positive)
        fn = len(positive - predicted)
        unknown = len(predicted - universe)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
        return {
            "tp": tp, "fp": fp, "fn": fn, "unknown_not_in_reference": unknown,
            "precision": precision, "precision_denominator": tp + fp,
            "recall": recall, "recall_denominator": tp + fn,
            "f1": f1, "f1_denominator": 2 * tp + fp + fn,
        }

    determinate_set = set(determinate)
    judged_determinate = judged & determinate_set
    coverage = len(judged_determinate) / len(determinate_set) if determinate_set else None
    return {
        "reference_composition": {
            "pairs": len(gold),
            "determinate": len(determinate),
            "required_total": len(gold_required),
            "required_direct": len(gold_direct),
            "required_indirect": len(gold_indirect),
            "support": len(gold_support),
            "negative": len(gold_negative),
            "uncertain": len(gold_uncertain),
        },
        "necessity": metrics(predicted_required, gold_required, set(determinate)),
        "directness": metrics(predicted_direct, gold_direct, set(determinate)),
        "model_abstentions": len(abstained),
        "verdict_coverage_on_determinate": coverage,
        "coverage_definition": (
            "|determinate reference pairs that received a non-abstaining verdict| / "
            "|determinate reference pairs|. A pair the model was never shown counts as uncovered, "
            "not as covered."
        ),
        "pairs_never_judged": len(determinate_set - judged),
        "pairs_abstained": len(abstained & determinate_set),
        "pairs_judged_determinate": len(judged_determinate),
        "pairs_judged_outside_reference": len(judged - determinate_set),
        "support_predicted_as_required": sorted(
            f"{a}->{b}" for a, b in (gold_support & predicted_required)
        ),
        "indirect_predicted_as_direct": sorted(
            f"{a}->{b}" for a, b in (gold_indirect & predicted_direct)
        ),
        "uncertain_pairs": sorted(f"{a}->{b}" for a, b in gold_uncertain),
    }


def frozen_metrics(pairs: list[dict], verdicts: list[dict], endpoints: list[dict]) -> dict:
    candidates = [
        {
            "source_ku": pair["target_ku"],
            "target_ku": pair["prerequisite_ku"],
            "source_name": pair.get("target_name"),
            "target_name": pair.get("prerequisite_name"),
        }
        for pair in pairs
    ]
    reference = [
        {
            "id": pair.get("id"),
            "target_ku": pair["target_ku"],
            "prerequisite_ku": pair["prerequisite_ku"],
            "role": pair["role"],
            "directness": pair.get("directness"),
            "provenance": pair.get("provenance") or "assistant_source_adjudication",
        }
        for pair in pairs
    ]
    scores = score_evaluation(
        {
            "endpoints": endpoints,
            "candidates": candidates,
            "reference": reference,
            "predictions": {"verdicts": verdicts},
        }
    )
    pool = scores["candidate_pool_review"]
    return {
        "necessity": {k: pool["requiredness"][k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
        "directness": {k: pool["directness"][k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
        "abstentions_in_scope_block": scores["scope"]["candidate_verdict_abstentions"],
        "scored_subset": scores["scope"]["candidate_pairs_scored"],
        "determinate_reference_pairs": scores["scope"]["determinate_reference_pair_count"],
        "support_false_accepts": scores["support_false_accepts"]["count"],
        "support_false_accept_denominator": scores["support_false_accepts"]["denominator"],
        "indirect_false_accepts": scores["indirect_false_accepts"]["count"],
        "indirect_false_accept_denominator": scores["indirect_false_accepts"]["denominator"],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "dsh.final_closeout.s1_rescore.v1",
        "no_new_model_calls": True,
        "scorer": "evaluation/prerequisite_candidate_retrieval_2026-10-07/run01/runtime_dependencies/"
                  "score_ku_prerequisite_evaluation.py (frozen, reused unchanged)",
        "columns": {
            "frozen_scorer": "unchanged frozen scorer; conditional on a non-abstaining verdict",
            "main": "determinate reference positives stay in the denominator; unlabelled predictions "
                    "are reported as unknown, not as false positives",
        },
        "references": {},
        "arms": {},
    }

    for version, spec in REFERENCE_VERSIONS.items():
        pairs, info = reference_pairs(version)
        info["note"] = spec["note"]
        info["pair_count"] = len(pairs)
        if pairs:
            info["role_counts"] = {}
            for pair in pairs:
                key = pair["role"] if pair["role"] != "required" else f"required_{pair.get('directness')}"
                info["role_counts"][key] = info["role_counts"].get(key, 0) + 1
        report["references"][version] = info

    for spec in ARMS:
        version = spec["reference_version"]
        pairs, _ = reference_pairs(version)
        if not pairs:
            report["arms"][spec["arm_id"]] = {"skipped": "reference version not available"}
            continue
        verdicts = arm_verdicts(spec, pairs)
        entry = {
            "reference_version": version,
            "predictions": spec["predictions"].relative_to(REPO).as_posix(),
            "verdict_count": len(verdicts),
            "note": spec["note"],
            "label_counts": {},
            "frozen_scorer": frozen_metrics(pairs, verdicts, endpoints_for(version)),
            "main": main_metrics(pairs, verdicts),
        }
        for verdict in verdicts:
            label = verdict["label"]
            entry["label_counts"][label] = entry["label_counts"].get(label, 0) + 1
        report["arms"][spec["arm_id"]] = entry

    # The same responses read against the adjudicated reference and its sensitivity variant, so the
    # delta is attributable to the reference revision alone.
    for spec in ARMS:
        if spec["reference_version"] != "new28_v2":
            continue
        pairs_v2, _ = reference_pairs("new28_v2")
        verdicts = arm_verdicts(spec, pairs_v2)
        for variant, suffix in (
            ("new28_v3", "_vs_v3"),
            ("new28_v3_n04_required", "_vs_v3_n04_required"),
        ):
            pairs_variant, _ = reference_pairs(variant)
            if not pairs_variant:
                continue
            key = spec["arm_id"].replace(".ku", f".ku{suffix}").replace(".ctx", f".ctx{suffix}")
            report["arms"][key] = {
                "reference_version": variant,
                "predictions": spec["predictions"].relative_to(REPO).as_posix(),
                "verdict_count": len(verdicts),
                "note": "Identical model responses, rescored against the adjudicated reference.",
                "frozen_scorer": frozen_metrics(pairs_variant, verdicts, endpoints_for(variant)),
                "main": main_metrics(pairs_variant, verdicts),
            }

    out = OUT_DIR / "rescore.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 先修实验重评分：冻结评分器口径 vs 主口径",
        "",
        "冻结评分器只在「有确定参考标签且模型未弃判」的子集上计算，因此召回的分母是候选子集而不是全部确定正例。",
        "主口径把全部确定正例留在召回分母里（弃判算漏检），并把不在参考里的预测单列 `unknown`，不计假阳性。",
        "本表不含任何新模型调用：同一批既有响应，只换参考版本与统计口径。",
        "",
        "| 组 | 参考 | 冻结口径 必要性 P/R/F1 | 主口径 必要性 P/R/F1 | 冻结口径 直接性 F1 | 主口径 直接性 F1 | 弃判 | 判定覆盖率 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm_id, entry in report["arms"].items():
        if "skipped" in entry:
            continue
        f, m = entry["frozen_scorer"], entry["main"]
        def fmt(block):
            def show(value):
                return "n/a" if value is None else f"{value:.3f}"
            return f"{show(block['precision'])}/{show(block['recall'])}/{show(block['f1'])}"
        lines.append(
            f"| {arm_id} | {entry['reference_version']} | {fmt(f['necessity'])} | {fmt(m['necessity'])} "
            f"| {fmt(f['directness'])} | {fmt(m['directness'])} | {m['model_abstentions']} "
            f"| {m['verdict_coverage_on_determinate']:.2f} |"
        )
    lines += [
        "",
        "## 参考组成",
        "",
        "| 版本 | 对数 | 必需(直接/间接) | 支持类 | 负例 | 未决 |",
        "|---|---|---|---|---|---|",
    ]
    for version, info in report["references"].items():
        if not info.get("available"):
            lines.append(f"| {version} | (不可用) | | | | |")
            continue
        counts = info.get("role_counts", {})
        lines.append(
            f"| {version} | {info.get('pair_count')} "
            f"| {counts.get('required_direct', 0)}/{counts.get('required_indirect', 0)} "
            f"| {counts.get('principle_support', 0) + counts.get('proof_support', 0)} "
            f"| {counts.get('related_or_none', 0)} | {counts.get('uncertain', 0)} |"
        )
    lines += [
        "",
        "## 关键读法",
        "",
        "1. 旧 27 对参考未发现同类口径冲突：15 条必需里没有一条以「证明用到」立论，两条证明性依赖",
        "   （N03/N04 积分上限函数及其导数）本来就记 proof_support。因此旧参考不修订，A/B 两组的",
        "   新旧口径完全一致（弃判 0、覆盖率 1.00）。",
        "2. 新 28 对参考修订后，两种审核条件的精度都从 1.000 降到 0.700：模型把 N01、N15、N23",
        "   判成必需，而这三条正是原参考违反目标范围或目录粒度的地方。",
        "3. 上下文条件的直接性优势在修订后消失并反转：v2 下 ctx 0.842 > ku 0.778，v3 下 ctx 0.667",
        "   < ku 0.706。原因是 ctx 把 N15 从 principle_support 改成 required/direct，在原参考里被算成",
        "   一个新的直接命中，在修订后变成支持类误收。",
        "4. 真实先修管线的「F1 = 1.0」是条件化数字：冻结评分器只统计 9 个（基线 6 个）已提议且有",
        "   确定标签的对，而参考有 15 条必需。主口径下语义补全 R=0.600（9/15）、基线 R=0.400（6/15），",
        "   即补全把命中从 6 条提到 9 条，而不是把 F1 从 0.706 提到 0.900（后者是直接性子集口径）。",
    ]
    table = OUT_DIR / "table.md"
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {table.relative_to(REPO).as_posix()}")

    print(f"wrote {out.relative_to(REPO).as_posix()}")
    for arm_id, entry in report["arms"].items():
        if "skipped" in entry:
            print(f"  {arm_id:16s} SKIPPED")
            continue
        frozen = entry["frozen_scorer"]["necessity"]
        main = entry["main"]["necessity"]
        frozen_d = entry["frozen_scorer"]["directness"]
        main_d = entry["main"]["directness"]
        print(
            f"  {arm_id:16s} frozen necessity P={frozen['precision']} R={frozen['recall']} "
            f"F1={frozen['f1']} | main necessity P={main['precision']} R={main['recall']} F1={main['f1']} "
            f"| frozen dir F1={frozen_d['f1']} | main dir F1={main_d['f1']} "
            f"| abstain={entry['main']['model_abstentions']} coverage={entry['main']['verdict_coverage_on_determinate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
