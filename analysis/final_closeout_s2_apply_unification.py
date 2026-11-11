#!/usr/bin/env python
"""R1: apply the endpoint-carrier rule patches and recompute the two-column table.

The unification pass produced one patch file per method.  This script applies them to the frozen
judgements *programmatically* (so the resulting numbers are machine-derived rather than copied from a
summary), verifies that every patched row's ``old_semantic`` matches the frozen value, and reports
the pre-unification and post-unification distributions side by side.

Scope note that must travel with these numbers: the patch covers the rows whose endpoint/carrier
handling was identified as divergent.  A wider reading of the same rule -- every row where an
endpoint's presence is arguable -- was produced in a parallel pass and yields different
distributions; those are recorded as a sensitivity band, not silently dropped.

Read-only with respect to the judgements.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review/.

Usage:
    python analysis/final_closeout_s2_apply_unification.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review"

GRAPHS = ["P06", "P05", "P08", "P10", "Q04", "Q02", "V01", "V02"]
METHOD_OF = {
    "P06": "maf", "P05": "maf", "P08": "direct", "P10": "direct",
    "Q04": "edc", "Q02": "edc", "V01": "itext2kg", "V02": "itext2kg",
}
SEMANTIC_VALUES = ["full", "partial", "mentioned_only", "not_covered", "uncertain"]

# Wider application of the same rule, produced in a parallel pass whose per-row file was superseded.
# Recorded here as a sensitivity band only; the applied patch is the one on disk.
WIDE_SENSITIVITY = {
    "direct": {"P08": {"full": 7, "partial": 9, "mentioned_only": 11, "not_covered": 20},
               "P10": {"full": 9, "partial": 5, "mentioned_only": 15, "not_covered": 18}},
    "maf": {"P06": {"full": 11, "partial": 11, "mentioned_only": 13, "not_covered": 12},
            "P05": {"full": 12, "partial": 6, "mentioned_only": 17, "not_covered": 12}},
    "edc": {"Q04": {"full": 1, "partial": 8, "mentioned_only": 25, "not_covered": 13},
            "Q02": {"full": 1, "partial": 1, "mentioned_only": 23, "not_covered": 22}},
    "itext2kg": {"V01": {"full": 6, "partial": 3, "mentioned_only": 12, "not_covered": 26},
                 "V02": {"full": 3, "partial": 3, "mentioned_only": 3, "not_covered": 38}},
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    patches: dict[str, dict] = {}
    for path in sorted(OUT_DIR.glob("rule_unification_*.json")):
        data = read_json(path)
        rows = data.get("affected_rows") or data.get("decisions") or []
        for row in rows:
            patches.setdefault(row["graph"], {})[row["reference_id"]] = row

    problems: list[str] = []
    per_graph: dict[str, dict] = {}
    applied = 0
    changed = 0
    for label in GRAPHS:
        judgement = read_json(OUT_DIR / f"judgements_{label}.json")
        before = [row["semantic"] for row in judgement["judgements"]]
        after = list(before)
        graph_patches = patches.get(label, {})
        for index, row in enumerate(judgement["judgements"]):
            patch = graph_patches.get(row["reference_id"])
            if not patch:
                continue
            applied += 1
            if patch["old_semantic"] != row["semantic"]:
                problems.append(
                    f"{label}/{row['reference_id']}: patch old_semantic {patch['old_semantic']!r} "
                    f"!= frozen {row['semantic']!r}"
                )
            if patch.get("new_semantic") != row["semantic"]:
                changed += 1
            after[index] = patch.get("new_semantic", row["semantic"])

        def counts(values: list[str]) -> dict:
            return {value: values.count(value) for value in SEMANTIC_VALUES}

        before_counts = counts(before)
        after_counts = counts(after)
        covered_before = before_counts["full"] + before_counts["partial"]
        covered_after = after_counts["full"] + after_counts["partial"]
        per_graph[label] = {
            "method": METHOD_OF[label],
            "patched_rows": len(graph_patches),
            "before": before_counts,
            "after": after_counts,
            "covered_before": covered_before,
            "covered_after": covered_after,
            "total": len(before),
        }

    per_method: dict[str, dict] = {}
    for method in sorted(set(METHOD_OF.values())):
        labels = [label for label in GRAPHS if METHOD_OF[label] == method]
        before = {value: sum(per_graph[label]["before"][value] for label in labels)
                  for value in SEMANTIC_VALUES}
        after = {value: sum(per_graph[label]["after"][value] for label in labels)
                 for value in SEMANTIC_VALUES}
        wide = {}
        if method in WIDE_SENSITIVITY:
            wide = {
                value: sum(WIDE_SENSITIVITY[method][label].get(value, 0) for label in labels)
                for value in SEMANTIC_VALUES
            }
        total = sum(before.values())
        per_method[method] = {
            "relations": total,
            "before": before,
            "after": after,
            "covered_before": before["full"] + before["partial"],
            "covered_after": after["full"] + after["partial"],
            "wide_reading_after": wide or None,
            "wide_covered_after": (wide["full"] + wide["partial"]) if wide else None,
        }

    payload = {
        "schema": "dsh.final_closeout.s2_unified_semantics.v1",
        "rule_version": "endpoint-carrier v2",
        "purpose": (
            "Apply the endpoint/carrier rule patches to the frozen judgements and report the "
            "semantic column before and after unification, per graph and per method."
        ),
        "applied_patch_files": sorted(p.name for p in OUT_DIR.glob("rule_unification_*.json")),
        "patched_rows": applied,
        "changed_rows": changed,
        "patch_consistency": {"problems": problems, "ok": not problems},
        "scope_limitation": (
            "The applied patch covers the rows whose endpoint/carrier handling was identified as "
            "divergent. A wider application of the same rule to every row whose endpoint presence is "
            "arguable was produced in a parallel pass; its per-row file was superseded, so only its "
            "distribution is recorded, as wide_reading_after. The choice between the two does not "
            "affect the structural conclusions (EDC not_comparable, iText strict conforms 0) and "
            "neither reading establishes a between-method ranking."
        ),
        "per_graph": per_graph,
        "per_method": per_method,
    }
    out = OUT_DIR / "metrics_unified.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 端点/承载规则统一后的语义列（R1）",
        "",
        "规则：`endpoint-carrier v2` —— 端点是否出现按**内容对象**判定，不按绑定集合；"
        "「内容相近」不等于「同一对象」。严格列不受影响。",
        "",
        f"应用的补丁文件：{', '.join(payload['applied_patch_files'])}；"
        f"打补丁 {applied} 行，其中实际改变 {changed} 行；一致性检查 "
        f"{'通过' if not problems else '存在问题'}。",
        "",
        "## 按方法（两轮合并，各 94 条关系）",
        "",
        "| 方法 | 统一前 full+partial | 统一后 full+partial | 统一后分布 full/partial/mentioned_only/not_covered | 宽口径敏感性 full+partial |",
        "|---|---|---|---|---|",
    ]
    for method, block in per_method.items():
        after = block["after"]
        wide = block["wide_covered_after"]
        lines.append(
            f"| {method} | {block['covered_before']}/94 | {block['covered_after']}/94 "
            f"| {after['full']}/{after['partial']}/{after['mentioned_only']}/{after['not_covered']} "
            f"| {wide}/94 |" if wide is not None else
            f"| {method} | {block['covered_before']}/94 | {block['covered_after']}/94 "
            f"| {after['full']}/{after['partial']}/{after['mentioned_only']}/{after['not_covered']} "
            f"| n/a |"
        )
    lines += [
        "",
        "## 按图",
        "",
        "| 图 | 方法 | 打补丁行 | 统一前 full+partial | 统一后 full+partial |",
        "|---|---|---|---|---|",
    ]
    for label in GRAPHS:
        block = per_graph[label]
        lines.append(
            f"| {label} | {block['method']} | {block['patched_rows']} "
            f"| {block['covered_before']}/47 | {block['covered_after']}/47 |"
        )
    lines += [
        "",
        "## 范围限制（必须与这些数字一起出现）",
        "",
        "1. 应用的补丁覆盖的是**已被识别为口径分歧**的行；同一规则更宽的读法（把所有端点是否出现可争的",
        "   行都重审）在并行一轮里产生过不同分布，其逐行文件已被取代，故只把分布记在上表的"
        "   「宽口径敏感性」列。",
        "2. 两种读法**都不改变结构性结论**：EDC 的严格列 94/94 不可比、iText 的严格 `conforms` 为 0。",
        "3. 两种读法**都不建立方法间排名**：同一方法的两次重复是不同采样图，生成差异与判定差异混在一起。",
        "4. 严格列一行未改。",
    ]
    (OUT_DIR / "metrics_unified.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {out.relative_to(REPO).as_posix()}")
    print(f"  patch files: {payload['applied_patch_files']}")
    print(f"  patched rows={applied} changed={changed} consistency_ok={not problems}")
    for problem in problems[:6]:
        print(f"   ! {problem}")
    for method, block in per_method.items():
        after = block["after"]
        print(f"  {method:9s} covered {block['covered_before']} -> {block['covered_after']}/94 "
              f"(full {after['full']}, partial {after['partial']}, "
              f"mentioned_only {after['mentioned_only']}, not_covered {after['not_covered']}) "
              f"| wide {block['wide_covered_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
