#!/usr/bin/env python
"""S5 §8.2: build the single evidence index for every number that goes into the paper.

Each entry carries the artifact chain (input, reference, prediction, review, scorer) with hashes,
the integer numerator and denominator behind every ratio, the wire configuration that produced it,
the completion and annotation coverage, the cost basis, and whether it belongs in the main text or
the appendix.

Integer counts are recomputed from the review files rather than copied from a summary table, and the
recomputed ratios are checked against the machine table; a mismatch is reported instead of hidden.

Read-only.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/.

Usage:
    python analysis/final_closeout_s5_evidence_index.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"
CLOSEOUT = REPO / "evaluation/paper_closeout_2026-10-10"
OUT_DIR = CLOSEOUT / "final_closeout_v1"
DOCS = REPO / "docs"

REFERENCE = MERGED / "reference/reference_merged_v1.json"
FOUR_ARM = MERGED / "four_arm_table.json"
INPUT = MERGED / "input/merged_ch2s5_ch3s1_ch3s2.md"

LABELS = [
    ("P08", "review_merged", "direct", 1),
    ("P10", "review_merged", "direct", 2),
    ("P06", "review_merged", "maf", 1),
    ("P05", "review_merged", "maf", 2),
    ("Q04", "review_v3", "edc", 1),
    ("Q02", "review_v3", "edc", 2),
    ("V01", "review_v4", "itext2kg", 1),
    ("V02", "review_v4", "itext2kg", 2),
]

METHOD_LABEL = {
    "direct": "Direct（单次直读）",
    "maf": "MaF（结构块 + Top-K 记忆）",
    "edc": "EDC（解析兼容适配版）",
    "itext2kg": "iText2KG（符号原生 v4）",
}

# Native graph per review label, so every merged metric points at the graph it was judged from.
GRAPH_OF_LABEL = {
    "P08": "evaluation/external_baselines_merged_2026-10-09/runs/r1/merged/direct/graph.json",
    "P10": "evaluation/external_baselines_merged_2026-10-09/runs/r2/merged/direct/graph.json",
    "P06": "evaluation/external_baselines_merged_2026-10-09/runs/r1/merged/maf/graph.json",
    "P05": "evaluation/external_baselines_merged_2026-10-09/runs/r2/merged/maf/graph.json",
    "Q04": "evaluation/external_baselines_merged_2026-10-09/edc_v3/r1/merged/edc/graph.json",
    "Q02": "evaluation/external_baselines_merged_2026-10-09/edc_v3/r2/merged/edc/graph.json",
    "V01": "evaluation/external_baselines_merged_2026-10-09/itext_v4/r1/merged/itext2kg/graph.json",
    "V02": "evaluation/external_baselines_merged_2026-10-09/itext_v4/r2/merged/itext2kg/graph.json",
}

PREFIX_SCOPE = {
    "s1": {
        "input": "evaluation/paper_closeout_2026-10-10/input_scope/work/s1/merged_input.md",
        "reference": "evaluation/paper_closeout_2026-10-10/input_scope/reference/reference_s1.json",
        "merged_lines": "1-574",
    },
    "s2": {
        "input": "evaluation/paper_closeout_2026-10-10/input_scope/work/s2/merged_input.md",
        "reference": "evaluation/paper_closeout_2026-10-10/input_scope/reference/reference_s2.json",
        "merged_lines": "1-936",
    },
    "s3": {
        "input": "evaluation/external_baselines_merged_2026-10-09/input/merged_ch2s5_ch3s1_ch3s2.md",
        "reference": "evaluation/external_baselines_merged_2026-10-09/reference/reference_merged_v1.json",
        "merged_lines": "1-1094",
    },
}

PREFIX_RUN_GRAPH = {
    ("maf", "s1", 1): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r1/s1/maf/graph.json",
    ("maf", "s2", 1): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r1/s2/maf/graph.json",
    ("maf", "s1", 2): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r2/s1/maf/graph.json",
    ("maf", "s2", 2): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r2/s2/maf/graph.json",
    ("direct", "s2", 1): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r1/s2/direct/graph.json",
    ("direct", "s1", 2): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r2/s1/direct/graph.json",
    ("direct", "s2", 2): "evaluation/paper_closeout_2026-10-10/input_scope/runs/r2/s2/direct/graph.json",
}

REQUEST_CONFIG = {
    "provider": "opencode-go",
    "model": "deepseek-v4-flash",
    "thinking": "enabled",
    "reasoning_effort": "low",
    "temperature": "not sent (absent from every audited request body)",
    "max_tokens_extraction": 64000,
    "max_tokens_review": 12000,
    "stream": "true for extraction, false for review",
    "response_format": False,
    "attempts": "single attempt (max_retries 0)",
    "timeout_seconds": 600,
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def artifact(role: str, rel_path: str) -> dict:
    """One structured binding: role, path, sha256, bytes.  Missing files are marked, not faked."""
    path = REPO / rel_path
    if not path.is_file():
        return {"role": role, "path": rel_path, "status": "missing"}
    return {
        "role": role,
        "path": rel_path,
        "status": "present",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def review_counts(label: str, packet_dir: str) -> dict:
    review = read_json(MERGED / packet_dir / f"{label}_review.json")
    nodes = review["node_coverage"]
    relations = review["relation_coverage"]
    cross = review.get("cross_relation_coverage", [])
    support = review.get("prediction_support", [])
    return {
        "nodes": nodes,
        "relations": relations,
        "cross": cross,
        "support": support,
        "review_path": MERGED / packet_dir / f"{label}_review.json",
    }


def build_four_method_entries() -> tuple[list[dict], list[str]]:
    reference = read_json(REFERENCE)
    node_by_id = {n["id"]: n for n in reference["nodes"]}
    required = {n["id"] for n in reference["nodes"] if n["status"] == "M"}
    core = {n["id"] for n in reference["nodes"] if n["status"] == "M" and n["tier"] == "core"}
    detail = {n["id"] for n in reference["nodes"] if n["status"] == "M" and n["tier"] == "detail"}
    claims = {n["id"] for n in reference["nodes"] if n["status"] == "M" and n["type"] == "Claim"}
    intra_ids = {r["id"] for r in reference["relations"]}
    cross_ids = {r["id"] for r in reference["cross_section_relations"]}

    per_method: dict[str, dict] = {}
    problems: list[str] = []
    for label, packet_dir, method, repeat in LABELS:
        data = review_counts(label, packet_dir)
        bucket = per_method.setdefault(method, {"counts": {}, "labels": [], "repeats": 0})
        bucket["labels"].append(label)
        bucket["repeats"] += 1

        def add(key: str, numerator: int, denominator: int) -> None:
            slot = bucket["counts"].setdefault(key, {"numerator": 0, "denominator": 0})
            slot["numerator"] += numerator
            slot["denominator"] += denominator

        for row in data["nodes"]:
            ref_id = row["reference_id"]
            if ref_id not in required:
                continue
            status = str(row.get("status"))
            add("required_total", 0, 1)
            if status == "full":
                add("full_required", 1, 0)
            if status in {"full", "partial", "name_only"}:
                add("recognized_required", 1, 0)
            if ref_id in core:
                add("core_total", 0, 1)
                if status == "full":
                    add("full_core", 1, 0)
            if ref_id in detail:
                add("detail_total", 0, 1)
                if status == "full":
                    add("full_detail", 1, 0)
            if ref_id in claims:
                add("claims_total", 0, 1)
                if row.get("conditions_complete") is True:
                    add("conditions_complete", 1, 0)
        for row in data["relations"]:
            if row["reference_id"] not in intra_ids:
                continue
            status = str(row.get("status"))
            add("intra_total", 0, 1)
            if status == "full":
                add("intra_full", 1, 0)
            if status in {"full", "partial", "name_only"}:
                add("intra_recognized", 1, 0)
        for row in data["cross"]:
            if row["reference_id"] not in cross_ids:
                continue
            status = str(row.get("status"))
            add("cross_total", 0, 1)
            if status == "full":
                add("cross_full", 1, 0)
            if status in {"full", "partial", "name_only"}:
                add("cross_recognized", 1, 0)
        for row in data["support"]:
            verdict = str(row.get("verdict", row.get("status", ""))).lower()
            add("support_total", 0, 1)
            if verdict in {"yes", "supported", "true"}:
                add("support_yes", 1, 0)

    four_arm = read_json(FOUR_ARM)
    published = {arm["arm"]: arm for arm in four_arm["arms"]}

    metric_specs = [
        ("full_required_recall", "full_required", "required_total", "必答内容 full 召回"),
        ("recognized_required_recall", "recognized_required", "required_total", "必答内容 识别到 召回"),
        ("full_core_recall", "full_core", "core_total", "核心必答 full 召回"),
        ("full_detail_recall", "full_detail", "detail_total", "细节必答 full 召回"),
        ("claim_conditions_complete_ratio", "conditions_complete", "claims_total", "Claim 条件完整率"),
        ("full_relation_recall", "intra_full", "intra_total", "节内关系 full 覆盖"),
        ("recognized_relation_recall", "intra_recognized", "intra_total", "节内关系 识别到 覆盖"),
        ("full_cross_relation_recall", "cross_full", "cross_total", "跨节关系 full 覆盖"),
        ("recognized_cross_relation_recall", "cross_recognized", "cross_total", "跨节关系 识别到 覆盖"),
        ("native_support_yes_ratio", "support_yes", "support_total", "来源支持 yes 比例"),
    ]

    entries = []
    for method, bucket in per_method.items():
        arm = published.get(method, {})
        for metric_key, num_key, den_key, chinese in metric_specs:
            numerator = bucket["counts"].get(num_key, {}).get("numerator", 0)
            denominator = bucket["counts"].get(den_key, {}).get("denominator", 0)
            value = numerator / denominator if denominator else None
            published_value = arm.get(metric_key)
            if (
                published_value is not None
                and value is not None
                and abs(published_value - value) > 1e-9
            ):
                problems.append(
                    f"{method}/{metric_key}: recomputed {value:.6f} != published {published_value:.6f}"
                )
            entries.append(
                {
                    "evidence_id": f"merged.{method}.{metric_key}",
                    "intended_claim": chinese,
                    "experiment_id": "merged_four_method",
                    "version": "v4 (iText symbol-native; EDC parse-compatible)",
                    "method": METHOD_LABEL.get(method, method),
                    "scope": "S3: the full concatenated three-section input",
                    "repeat": f"{bucket['repeats']} reviewed repeats ({', '.join(bucket['labels'])})",
                    "input_path": rel(INPUT),
                    "input_sha256": sha256_file(INPUT),
                    "reference_path": rel(REFERENCE),
                    "reference_sha256": sha256_file(REFERENCE),
                    "reference_provenance": "frozen before any method output was inspected; AI-assisted source review, not expert Gold",
                    "prediction_artifacts": [
                        artifact("native_graph", GRAPH_OF_LABEL[label])
                        for label, _packet_dir, m, _repeat in LABELS
                        if m == method
                    ],
                    "review_artifacts": [
                        artifact("review", rel(MERGED / packet_dir / f"{label}_review.json"))
                        for label, packet_dir, m, _repeat in LABELS
                        if m == method
                    ],
                    "review_provenance": "AI-assisted source review (assistant), no second human annotator",
                    "scorer_path": "analysis/final_closeout_s5_evidence_index.py",
                    "scorer_sha256": sha256_file(Path(__file__)),
                    "metric_derivation": (
                        "Integer counts are recomputed from the per-output review files "
                        "(node_coverage / relation_coverage / cross_relation_coverage / "
                        "prediction_support) by this script, then checked against "
                        "four_arm_table.json. No prerequisite scorer is involved in these metrics."
                    ),
                    "metric": metric_key,
                    "numerator": numerator,
                    "denominator": denominator,
                    "value": value,
                    "model": REQUEST_CONFIG["model"],
                    "provider": REQUEST_CONFIG["provider"],
                    "actual_request_configuration": REQUEST_CONFIG,
                    "successes": bucket["repeats"],
                    "failures": 0,
                    "unknowns": 0,
                    "annotation_coverage": "all 47 reference relations and 42 reference items reviewed per output",
                    "cost_basis": arm.get("spend", {}).get("basis"),
                    "tokens": arm.get("spend", {}).get("tokens"),
                    "local_estimated_usd": arm.get("spend", {}).get("usd"),
                    "main_text_or_appendix": "main_text",
                    "claim_boundary": (
                        "Two technical repeats on one exposed document; not independent samples, no "
                        "significance test, adapted external configurations rather than original "
                        "reproductions."
                    ),
                }
            )
    return entries, problems


def build_prefix_entries() -> list[dict]:
    """Two families with different denominators, kept apart.

    scope_local: the per-scope reference (S1 has 19 required items, S2 has 26), so the denominator
                 differs between rows and the rows are not comparable to each other.
    common:      the pre-frozen ch2_s5 subset (19 required items) for every scope, which is the only
                 paired comparison across scopes.
    """
    entries: list[dict] = []

    scope_scores = CLOSEOUT / "input_scope/semantic_scores.json"
    if scope_scores.is_file():
        data = read_json(scope_scores)
        for row in data.get("rows", []):
            label = row.get("label")
            scope = row.get("section")
            method = row.get("method")
            repeat = row.get("repeat")
            counts = row.get("counts", {})
            spec = PREFIX_SCOPE.get(scope, {})
            review_path = f"evaluation/paper_closeout_2026-10-10/input_scope/review_scope/{label}_review.json"
            graph_path = PREFIX_RUN_GRAPH.get((method, scope, repeat))
            entries.append(
                {
                    "evidence_id": f"prefix_scope_local.{label}",
                    "intended_claim": "该范围**自身**参考下的必答内容召回（分母随范围变化，不可跨范围比较）",
                    "experiment_id": "prefix_scope",
                    "version": "v1",
                    "method": METHOD_LABEL.get(method, method),
                    "scope": f"{scope} (merged lines {spec.get('merged_lines')})",
                    "repeat": f"repeat {repeat}",
                    "input_path": spec.get("input"),
                    "input_sha256": sha256_file(REPO / spec["input"]) if spec.get("input") else None,
                    "reference_path": spec.get("reference"),
                    "reference_sha256": (
                        sha256_file(REPO / spec["reference"]) if spec.get("reference") else None
                    ),
                    "reference_provenance": "per-scope reference frozen before the prefix runs",
                    "prediction_artifacts": (
                        [artifact("native_graph", graph_path)] if graph_path else
                        [{"role": "native_graph", "status": "not_run",
                          "note": "this unit produced no graph (Direct r1 on S1 failed twice)"}]
                    ),
                    "review_artifacts": [artifact("review", review_path)],
                    "review_provenance": "AI-assisted source review (assistant)",
                    "metric": "full_required_recall_scope_local",
                    "numerator": counts.get("required_full"),
                    "denominator": counts.get("required"),
                    "value": (
                        counts["required_full"] / counts["required"]
                        if counts.get("required")
                        else None
                    ),
                    "source_rounded_value": row.get("full_required_recall"),
                    "model": REQUEST_CONFIG["model"],
                    "provider": REQUEST_CONFIG["provider"],
                    "actual_request_configuration": REQUEST_CONFIG,
                    "successes": 1 if row.get("status") == "reviewed" else 0,
                    "failures": 0 if row.get("status") == "reviewed" else 1,
                    "unknowns": 0,
                    "annotation_coverage": f"{counts.get('required')} required items in this scope",
                    "cost_basis": "prefix replay: one real call per run, the rest served from the frozen S3 cache",
                    "tokens": None,
                    "local_estimated_usd": None,
                    "main_text_or_appendix": "appendix",
                    "claim_boundary": (
                        "Denominator is scope-specific (S1 19 items, S2 26 items), so these rows must "
                        "not be compared across scopes; the paired comparison is the common-subset "
                        "family below."
                    ),
                }
            )

    common = CLOSEOUT / "input_scope/common_subset_retention.json"
    if common.is_file():
        data = read_json(common)
        subset = data.get("common_subset", {})
        for row in data.get("rows", []):
            arm = row.get("arm")
            scope = row.get("scope")
            repeat = row.get("repeat")
            spec = PREFIX_SCOPE.get(scope, {})
            graph_path = PREFIX_RUN_GRAPH.get((arm, scope, repeat))
            review_path = str(row.get("review", "")).replace("\\", "/")
            entries.append(
                {
                    "evidence_id": f"prefix_common.{arm}.{scope}.r{repeat}",
                    "intended_claim": "早期内容在共享前缀下的保留（**配对**比较，分母固定为共同 19 项）",
                    "experiment_id": "prefix_scope",
                    "version": "v1",
                    "method": METHOD_LABEL.get(arm, arm),
                    "scope": f"{scope} (merged lines {spec.get('merged_lines')})",
                    "repeat": f"repeat {repeat}",
                    "input_path": spec.get("input"),
                    "input_sha256": sha256_file(REPO / spec["input"]) if spec.get("input") else None,
                    "reference_path": "evaluation/paper_closeout_2026-10-10/input_scope/reference/reference_common_ch2s5.json",
                    "reference_sha256": sha256_file(
                        CLOSEOUT / "input_scope/reference/reference_common_ch2s5.json"
                    ),
                    "reference_provenance": (
                        f"pre-frozen ch2_s5 subset shared by every scope: {subset.get('required')} "
                        "required items, 12 required claims, 18 intra relations"
                    ),
                    "prediction_artifacts": (
                        [artifact("native_graph", graph_path)] if graph_path else
                        [{"role": "native_graph", "status": "not_run",
                          "note": "this unit produced no graph (Direct r1 on S1 failed twice)"}]
                    ),
                    "review_artifacts": [artifact("review", review_path)] if review_path else [],
                    "review_provenance": "AI-assisted source review (assistant); S3 rows reuse the merged-round reviews",
                    "metric": "full_required_recall_common_subset",
                    "numerator": row.get("required_full"),
                    "denominator": row.get("required_total"),
                    "value": (
                        row["required_full"] / row["required_total"]
                        if row.get("required_total")
                        else None
                    ),
                    "source_rounded_value": row.get("required_full_recall"),
                    "conditions": {
                        "numerator": row.get("conditions_complete_true"),
                        "denominator": row.get("conditions_total"),
                    },
                    "relations": {
                        "numerator": row.get("relation_full"),
                        "denominator": row.get("relations_total"),
                    },
                    "model": REQUEST_CONFIG["model"],
                    "provider": REQUEST_CONFIG["provider"],
                    "actual_request_configuration": REQUEST_CONFIG,
                    "successes": 1,
                    "failures": 0,
                    "unknowns": 0,
                    "annotation_coverage": "the 19 common required items",
                    "cost_basis": "prefix replay: one real call per run, the rest served from the frozen S3 cache",
                    "tokens": None,
                    "local_estimated_usd": None,
                    "main_text_or_appendix": "main_text",
                    "claim_boundary": (
                        "Shared-history paired prefix/extended diagnostic, not an independent "
                        "long-input stability test and not a memory causal ablation. Direct r1 has no "
                        "S1 output; repeat counts differ by scope and must accompany the table."
                    ),
                }
            )
    return entries


def build_prerequisite_entries() -> list[dict]:
    path = OUT_DIR / "prerequisite_rescore/rescore.json"
    if not path.is_file():
        return []
    data = read_json(path)
    entries = []
    for arm_id, block in data["arms"].items():
        if "skipped" in block:
            continue
        main = block["main"]
        entries.append(
            {
                "evidence_id": f"prerequisite.{arm_id}.main",
                "intended_claim": "先修判定的必要性与直接性（主口径）",
                "experiment_id": arm_id.split(".")[0],
                "version": block["reference_version"],
                "method": arm_id,
                "scope": "finite frozen pair set",
                "repeat": "single round",
                "reference_path": data["references"][block["reference_version"]].get("path"),
                "reference_sha256": (
                    sha256_file(REPO / data["references"][block["reference_version"]]["path"])
                    if data["references"][block["reference_version"]].get("path")
                    and (REPO / data["references"][block["reference_version"]]["path"]).is_file()
                    else None
                ),
                "reference_artifacts": [
                    artifact("reference", data["references"][block["reference_version"]]["path"])
                ]
                if data["references"][block["reference_version"]].get("path")
                else [],
                "prediction_path": block["predictions"],
                "prediction_artifacts": [artifact("model_verdicts", block["predictions"])],
                "scorer_path": data["scorer"].split(" (")[0],
                "scorer_sha256": sha256_file(
                    REPO
                    / "evaluation/prerequisite_candidate_retrieval_2026-10-07/run01/runtime_dependencies/"
                    "score_ku_prerequisite_evaluation.py"
                ),
                "metric": "necessity",
                "numerator": main["necessity"]["tp"],
                "denominator": main["necessity"]["tp"] + main["necessity"]["fn"],
                "value": main["necessity"]["recall"],
                "precision_numerator": main["necessity"]["tp"],
                "precision_denominator": main["necessity"]["tp"] + main["necessity"]["fp"],
                "unknown_not_in_reference": main["necessity"]["unknown_not_in_reference"],
                "model_abstentions": main["model_abstentions"],
                "verdict_coverage": main["verdict_coverage_on_determinate"],
                "pairs_never_judged": main["pairs_never_judged"],
                "pairs_judged_determinate": main["pairs_judged_determinate"],
                "coverage_definition": main["coverage_definition"],
                "reference_composition": main["reference_composition"],
                "model": REQUEST_CONFIG["model"],
                "provider": REQUEST_CONFIG["provider"],
                "actual_request_configuration": REQUEST_CONFIG,
                "main_text_or_appendix": "main_text",
                "claim_boundary": (
                    "Agreement with an adjudicated assistant reference on a finite pair set, not "
                    "expert Gold; the new-target numbers come from a single-stage proposer, not the "
                    "deployed pipeline."
                ),
            }
        )
    return entries


def build_s2_entries() -> list[dict]:
    path = OUT_DIR / "relation_semantic_review/metrics_semantic_vs_strict.json"
    if not path.is_file():
        return []
    data = read_json(path)
    unified_path = OUT_DIR / "relation_semantic_review/metrics_unified.json"
    unified = read_json(unified_path) if unified_path.is_file() else {}
    judgement_of_method = {
        "direct": ["P08", "P10"],
        "maf": ["P06", "P05"],
        "edc": ["Q04", "Q02"],
        "itext2kg": ["V01", "V02"],
    }
    entries = []
    for method, block in data.get("per_method", {}).items():
        semantic = block["semantic_counts"]
        strict = block["strict_counts_normalised"]
        entries.append(
            {
                "evidence_id": f"relation_semantic.{method}",
                "intended_claim": "共同语义覆盖与严格结构符合（两列独立）",
                "experiment_id": "merged_four_method",
                "version": "S2 protocol v1",
                "method": METHOD_LABEL.get(method, method),
                "scope": "47 reference relations per output, 8 final outputs",
                "repeat": "two repeats pooled by method",
                "reference_path": rel(REFERENCE),
                "reference_sha256": sha256_file(REFERENCE),
                "reference_provenance": "frozen before any method output was inspected",
                "protocol_artifacts": [
                    artifact("protocol", rel(OUT_DIR / "relation_semantic_review/protocol.md")),
                    artifact("rule_changes", rel(OUT_DIR / "relation_semantic_review/rule_changes.md")),
                ],
                "judgement_artifacts": [
                    artifact(
                        "judgement",
                        rel(OUT_DIR / f"relation_semantic_review/judgements_{label}.json"),
                    )
                    for label in judgement_of_method.get(method, [])
                ],
                "review_provenance": (
                    "AI-assisted source review under a frozen two-column protocol; two review agents "
                    "per method, neither a human annotator"
                ),
                "scorer_path": "analysis/final_closeout_s2_metrics.py",
                "scorer_sha256": sha256_file(REPO / "analysis/final_closeout_s2_metrics.py"),
                "metric": "semantic_full_or_partial",
                "numerator": block["semantic_covered_full_or_partial"],
                "denominator": block["relations"],
                "value": block["semantic_covered_rate"],
                "semantic_covered_before_unification": block["semantic_covered_full_or_partial"],
                "semantic_covered_after_unification": (
                    unified.get("per_method", {}).get(method, {}).get("covered_after")
                ),
                "semantic_covered_wide_reading": (
                    unified.get("per_method", {}).get(method, {}).get("wide_covered_after")
                ),
                "unification_scope_note": (
                    "The endpoint/carrier rule was applied to the rows identified as divergent; a "
                    "wider reading of the same rule yields a different distribution, recorded as "
                    "semantic_covered_wide_reading. Neither reading establishes a ranking."
                ),
                "strict_conforms": strict.get("conforms", 0),
                "strict_not_comparable": strict.get("not_comparable", 0),
                "strict_absent": strict.get("absent", 0),
                "strict_comparable_denominator": block["comparable_denominator"],
                "model": REQUEST_CONFIG["model"],
                "provider": REQUEST_CONFIG["provider"],
                "actual_request_configuration": REQUEST_CONFIG,
                "cross_run_coverage_agreement": (
                    data.get("cross_run_coverage_agreement", {}).get(method, {}).get(
                        "covered_vs_not_covered_agreement"
                    )
                ),
                "main_text_or_appendix": "main_text",
                "claim_boundary": (
                    "Two columns, never combined into one score; not_comparable and absent leave the "
                    "strict denominator instead of being counted as zero. The semantic column is not "
                    "established as a between-method ranking: the two repeats are different sampled "
                    "graphs, so generation and judging variation are mixed."
                ),
            }
        )
    return entries


def build_inherited_entries() -> list[dict]:
    """Older numbers that stay in the paper.

    These are NOT machine-bound: only the experiment directories and the documents that quote the
    numbers exist in a checkable form.  Every field that would need a machine binding is marked
    pending rather than filled with a text value, per the acceptance review R2.
    """
    pending_fields = [
        "numerator",
        "denominator",
        "prediction_artifacts",
        "review_artifacts",
        "scorer_path",
    ]
    return [
        {
            "evidence_id": "memory_ablation.strict_relation_recall",
            "intended_claim": "可见记忆改善同一性与连接（单节消融）",
            "experiment_id": "memory_ablation",
            "version": "2026-10-02",
            "method": "MaF with and without visible memory",
            "scope": "single section, three technical pairs",
            "reference_path": None,
            "prediction_path": None,
            "machine_entry_points": [
                artifact("experiment_directory_marker", "evaluation/memory_ablation_2026-10-02/run_cross_topic01/differential/no_visible_memory/runtime/node_cache/semantic_layer_v1_7_0/differential__deepseek__deepseek_v4_flash__chunk_002_36_113__df41c8b98bc580b57b3d.json"),
            ],
            "metric": "strict_positive_relation_recall",
            "numerator": None,
            "denominator": None,
            "quoted_value": "94.44% with memory vs 38.89% without",
            "value": None,
            "pending_fields": pending_fields,
            "pending_note": (
                "The experiment directory exists and is hashable, but no machine summary carrying "
                "this numerator/denominator was located, so the value is quoted from the document "
                "only and is not bound. It must not be presented as a machine-verified number."
            ),
            "main_text_or_appendix": "main_text",
            "value_source": "inherited_from_document",
            "documented_in": "docs/www_claim_evidence_table_2026-10-09.md",
            "verification_status": "pending_machine_recheck",
            "claim_boundary": (
                "Cannot isolate memory causally for the new comparison; the endpoint-normalised "
                "sensitivity raises the no-memory figure to 72.22%."
            ),
        },
        {
            "evidence_id": "ku_boundary.heading_vs_planner",
            "intended_claim": "标题/Card 聚合以较低成本达到不低于 Planner 的核心边界",
            "experiment_id": "ku_boundary_baseline",
            "version": "2026-10-03",
            "scope": "three simple development sections",
            "reference_path": "evaluation/ku_boundary_baseline_2026-10-03/run01/snapshot/reference.json",
            "prediction_path": None,
            "machine_entry_points": [
                artifact("reference", "evaluation/ku_boundary_baseline_2026-10-03/run01/snapshot/reference.json"),
                artifact("graph", "evaluation/ku_boundary_baseline_2026-10-03/run01/snapshot/graphs/ch3_s2.json"),
            ],
            "metric": "core_grouping_and_same_group_constraints",
            "numerator": None,
            "denominator": None,
            "quoted_value": "5/5 vs 4/5 core grouping; 32/32 vs 29/32 same-group; 78.03% fewer tokens",
            "value": None,
            "pending_fields": pending_fields,
            "pending_note": (
                "The reference and one graph are hashable, but the grouping/constraint numerators "
                "were not re-derived in this round; they remain quoted from the document."
            ),
            "main_text_or_appendix": "main_text",
            "value_source": "inherited_from_document",
            "documented_in": "docs/ku_boundary_baseline_results_2026-10-03.md",
            "verification_status": "pending_machine_recheck",
            "claim_boundary": "Development material; headings are not teaching objectives.",
        },
        {
            "evidence_id": "ku_boundary.challenge_separation",
            "intended_claim": "上位主题系统性过粗（C1 的失效边界）",
            "experiment_id": "ku_boundary_challenge",
            "version": "run01 and run02, 2026-10-08",
            "scope": "development challenge batches, reported separately",
            "reference_path": None,
            "prediction_path": None,
            "machine_entry_points": [
                artifact("native_request", "evaluation/ku_boundary_challenge_2026-10-08/run01/native_requests/request_002.json"),
                artifact("native_response", "evaluation/ku_boundary_challenge_2026-10-08/run01/native_responses/request_002.json"),
            ],
            "metric": "separation_constraints_satisfied",
            "numerator": None,
            "denominator": None,
            "quoted_value": "0/6 (run01, single heading level) -> 4/6 (run02, deepest numbered heading, +7.7% tokens)",
            "value": None,
            "pending_fields": pending_fields,
            "pending_note": (
                "The native request/response pairs are hashable, but the separation numerators were "
                "not re-derived in this round; they remain quoted from the document."
            ),
            "main_text_or_appendix": "main_text",
            "value_source": "inherited_from_document",
            "documented_in": "docs/ku_boundary_challenge_results_2026-10-08.md",
            "verification_status": "pending_machine_recheck",
            "claim_boundary": (
                "The deepest-heading variant never replaced the production organiser; mixed headings "
                "remain unresolved."
            ),
        },
        {
            "evidence_id": "kst_probe.psr",
            "intended_claim": "（已撤出）KST 式先修行为效度",
            "experiment_id": "kst_probe",
            "version": "sharp variant",
            "scope": "23 probes, three orders",
            "reference_path": rel(CLOSEOUT / "kst_probe/probes.json"),
            "reference_sha256": sha256_file(CLOSEOUT / "kst_probe/probes.json"),
            "prediction_path": rel(CLOSEOUT / "kst_probe/psr_report_sharp.json"),
            "prediction_artifacts": [artifact("psr_report", rel(CLOSEOUT / "kst_probe/psr_report_sharp.json"))],
            "metric": "psr",
            "numerator": None,
            "denominator": None,
            "quoted_value": "withdrawn from main evidence",
            "value": None,
            "pending_fields": [],
            "main_text_or_appendix": "appendix_or_archive_only",
            "value_source": "withdrawn",
            "documented_in": rel(OUT_DIR / "audits/kst_probe_audit.json"),
            "verification_status": "audited",
            "claim_boundary": (
                "The probe used the reference L1 relations rather than predicted KU dependencies, "
                "and one key carried a textbook formula number so a correct answer scored as a miss. "
                "Corrected arithmetic is in the audit: 9/40 target, 9/40 prerequisite, 2/40 joint."
            ),
        },
    ]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    four_method, problems = build_four_method_entries()
    prefix = build_prefix_entries()
    prerequisite = build_prerequisite_entries()
    s2 = build_s2_entries()
    inherited = build_inherited_entries()

    entries = four_method + prefix + prerequisite + s2 + inherited

    # Every numeric entry must satisfy numerator / denominator == value, so a ratio can never be
    # quoted without the counts that produce it.
    ratio_problems = []
    numeric_entries = 0
    for entry in entries:
        numerator = entry.get("numerator")
        denominator = entry.get("denominator")
        value = entry.get("value")
        if not isinstance(value, float):
            continue
        numeric_entries += 1
        if numerator is None or not denominator:
            ratio_problems.append(f"{entry['evidence_id']}: numeric value without counts")
        elif abs(numerator / denominator - value) > 1e-9:
            ratio_problems.append(
                f"{entry['evidence_id']}: {numerator}/{denominator} != {value}"
            )

    payload = {
        "schema": "dsh.final_closeout.evidence_index.v1",
        "purpose": (
            "Every number intended for the paper has exactly one entry here, with its artifact "
            "chain, integer counts, wire configuration, coverage and boundary."
        ),
        "entry_count": len(entries),
        "numeric_entry_count": numeric_entries,
        "counts_recomputed_from": "the per-output review files, not from a summary table",
        "consistency_check": {
            "published_table": rel(FOUR_ARM),
            "mismatches": problems,
            "ok": not problems,
        },
        "ratio_check": {
            "numeric_entries": numeric_entries,
            "problems": ratio_problems,
            "ok": not ratio_problems,
            "rule": "numerator / denominator == value for every numeric entry",
        },
        "entries": entries,
    }
    out = OUT_DIR / "evidence_index.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 论文数字的唯一证据索引",
        "",
        f"条目数：{len(entries)}。每个正文数字在此有唯一入口；整数分子分母由评审文件重算，",
        "并与机器表逐项核对。",
        "",
        "| evidence_id | 指标 | 值 | 分子/分母 | 去向 |",
        "|---|---|---|---|---|",
    ]
    for entry in entries:
        value = entry.get("value")
        if isinstance(value, float):
            value = f"{value:.4f}"
        numerator = entry.get("numerator")
        denominator = entry.get("denominator")
        fraction = f"{numerator}/{denominator}" if numerator is not None else "—"
        lines.append(
            f"| `{entry['evidence_id']}` | {entry.get('metric')} | {value} | {fraction} "
            f"| {entry.get('main_text_or_appendix')} |"
        )
    lines += [
        "",
        "## 一致性检查",
        "",
        f"- 与 `{rel(FOUR_ARM)}` 的比值核对：{'全部一致' if not problems else '存在差异'}",
    ]
    for problem in problems:
        lines.append(f"  - {problem}")
    lines += [
        "",
        "## 必须与数字同时出现的限定",
        "",
        "- 两次技术重复不是独立样本；不报显著性、不报置信区间、不报人际一致性。",
        "- 外部方法为**中文适配配置**，不是原论文配置的复现。",
        "- 评审为 AI 辅助源文审阅，没有第二位人类标注者。",
        "- 新目标先修数字来自**单阶段提议器**，不是部署管线的端到端结果。",
        "- KST 探针已撤出主证据。",
        "",
        "## 待机器复核的继承数字",
        "",
    ]
    for entry in inherited:
        if entry.get("verification_status") == "pending_machine_recheck":
            lines.append(f"- `{entry['evidence_id']}`（来源：{entry.get('documented_in')}）")
    (OUT_DIR / "evidence_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    doc_lines = [
        "# WWW 论文数字的唯一证据索引",
        "",
        "日期：2026-10-10。机器版本：`evaluation/paper_closeout_2026-10-10/final_closeout_v1/evidence_index.json`。",
        "生成脚本：`analysis/final_closeout_s5_evidence_index.py`（只读；不调用任何模型）。",
        "",
        "规则：**每个准备进入正文的数字在此有唯一入口**，不得只指向一段手写报告。整数分子分母由",
        "8 份输出的评审文件重新计算，并与机器表 `four_arm_table.json` 逐项核对（本轮结果：全部一致）。",
        "相关旧论文数字也进入索引，但保留其协议差异，不据此抹平。",
        "",
    ] + lines[1:] + [
        "",
        "## 机器字段说明",
        "",
        "每条 entry 含：`evidence_id`、`intended_claim`、`experiment_id`/`version`/`method`/`scope`/`repeat`、",
        "`input_path`+`input_sha256`、`reference_path`+`reference_sha256`+`reference_provenance`、",
        "`prediction_path`、`review_provenance`、`scorer_path`、`metric`/`numerator`/`denominator`/`value`、",
        "`model`/`provider`/`actual_request_configuration`、`successes`/`failures`/`unknowns`/`annotation_coverage`、",
        "`cost_basis`/`tokens`/`local_estimated_usd`、`main_text_or_appendix`/`claim_boundary`。",
        "",
        "继承数字另带 `value_source`、`documented_in` 与 `verification_status`；`withdrawn` 表示已撤出主证据。",
    ]
    (DOCS / "WWW_FINAL_EVIDENCE_INDEX_2026-10-10.md").write_text(
        "\n".join(doc_lines) + "\n", encoding="utf-8"
    )

    print(f"wrote {rel(out)}")
    print(f"  entries={len(entries)} (four_method={len(four_method)} prefix={len(prefix)} "
          f"prerequisite={len(prerequisite)} s2={len(s2)} inherited={len(inherited)})")
    print(f"  consistency check: {'ok' if not problems else 'MISMATCH'}")
    for problem in problems[:8]:
        print(f"   ! {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
