"""S0.4 select the E2 prerequisite targets and pre-register their handling.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md sections 4.1-4.5.

The plan fixed four targets. The author has since approved expanding the target
set to 8-10 units BEFORE the reference is frozen, because four targets yield only
five projection candidates and one of them yields none at all.

This script:
  1. runs the frozen deterministic projection offline (no model calls),
  2. reports the projection yield per candidate target,
  3. records the pre-registered target set and the pre-registered handling rules,
  4. records which units were already used as targets in the earlier round, so a
     reused target cannot be presented as a new one.

Outputs (new files only):
  evaluation/www_evidence_supplement_2026-10-11/e2_prerequisite/target_selection.json
  evaluation/www_evidence_supplement_2026-10-11/e2_prerequisite/target_selection.md
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
ROOT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
E2 = ROOT / "e2_prerequisite"
FROZEN = (
    WORKSPACE / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01"
)
REFERENCE_V3 = (
    WORKSPACE
    / "evaluation/paper_closeout_2026-10-10/final_closeout_v1"
    / "prerequisite_adjudication/reference_v3.json"
)
PROJECTION_SCRIPT = WORKSPACE / "scripts/build_ku_depends_candidates.py"
PROJECTION_OUT = E2 / "projection_candidates_frozen.json"

# Targets fixed by the plan (section 4.1).
PLAN_TARGETS = [
    "ku_fb8130a0dde2",
    "ku_da56c3fdccf2",
    "ku_e9f8c9c2740e",
    "ku_99bdf15cb98e",
]

# Targets used in the earlier 27-pair round (manuscript line 230: two
# improper-integral units and two homogeneous/reducible ODE units). A unit from
# this list must not be presented as a new target.
EARLIER_ROUND_TARGETS = [
    "ku_4ca81bb2e29f",  # 无穷限的反常积分
    "ku_43ddb748bc3e",  # 无界函数的反常积分
    "ku_e26c2fcd8de7",  # 齐次方程及其求解方法
    "ku_3f118916cc0a",  # 可化为齐次的方程及其求解
]

# Selected additions. Chosen on projection yield, topic spread, and whether the
# unit sits in a dense derivation cluster (where "derived from" and "prerequisite
# for applying" are most likely to be confused, which is the phenomenon under
# study). None of them was a target in the earlier round.
SELECTED_ADDITIONS = [
    {
        "unit_id": "ku_d43d41d9a5e8",
        "name": "拉格朗日中值定理",
        "chapter": "第三章",
        "why": "最高投影产出（6）。与已有的罗尔定理构成 derived_from 直连：罗尔定理是它的推导依据，"
               "但按应用型目标未必要先修——这正是论文要检验的推导/先修混淆。",
    },
    {
        "unit_id": "ku_bc75c2202ddf",
        "name": "泰勒公式及其余项",
        "chapter": "第三章",
        "why": "投影产出 4，且同时向柯西中值定理与洛必达法则发出 derived_from，"
               "把中值定理族与未定式连成一条推导链。",
    },
    {
        "unit_id": "ku_8dfb5403bb52",
        "name": "基本导数公式",
        "chapter": "第二章",
        "why": "投影产出 3。基础公式型内容，用于检验\"基础工具\"到底算先修还是背景，"
               "也是旧参考里出现过的非必需接受边的同类对象。",
    },
    {
        "unit_id": "ku_c9aad5c266bb",
        "name": "反函数的求导法则",
        "chapter": "第二章",
        "why": "投影产出 2，全部是 defined_in_terms_of，主题与其余目标不重叠。",
    },
    {
        "unit_id": "ku_f10444319956",
        "name": "积分上限的函数及其导数",
        "chapter": "第五章",
        "why": "投影产出 2，与已有的牛顿-莱布尼茨公式同章相邻，"
               "用于检验同一节内相邻单元的先修方向是否稳定。",
    },
]


def run_projection() -> dict:
    E2.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            str(PROJECTION_SCRIPT),
            str(FROZEN / "ku.json"),
            str(FROZEN / "graph.json"),
            "--out",
            str(PROJECTION_OUT),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return json.loads(PROJECTION_OUT.read_text(encoding="utf-8"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    catalog = json.loads((FROZEN / "catalog.json").read_text(encoding="utf-8"))
    names = {k["id"]: k["name"] for k in catalog["knowledge_units"]}
    chapters = {k["id"]: k.get("chapter", "") for k in catalog["knowledge_units"]}
    ref = json.loads(REFERENCE_V3.read_text(encoding="utf-8"))

    proj = run_projection()
    proj_by_source: dict[str, list] = defaultdict(list)
    for c in proj["candidates"]:
        proj_by_source[c["source_ku"]].append(c)

    ref_pairs_by_target = Counter(p["target_ku"] for p in ref["pairs"])

    selected = PLAN_TARGETS + [a["unit_id"] for a in SELECTED_ADDITIONS]

    # --- pre-registered handling rules -----------------------------------
    rules = {
        "reference_timing": "Every pair of the frozen reference is adjudicated BEFORE any "
                            "prediction of this round is generated. No pair is added, removed "
                            "or relabelled after seeing output.",
        "out_of_reference_predictions": "Predictions whose pair is absent from the frozen "
                                        "reference are neither automatically correct nor "
                                        "automatically wrong. They are frozen, then reviewed "
                                        "against source text in a separate pass, and reported "
                                        "as correct / incorrect / unresolved with their origin.",
        "uncertain_pair": "An unresolved reference pair is excluded from both the positive and "
                          "the negative denominator, and reported separately.",
        "not_generated_vs_abstained": "A positive that was never proposed, never reviewed, or "
                                      "on which the model abstained is still a miss for primary "
                                      "recall, but the three cases are counted separately.",
        "targets_reused_from_earlier_round": "A unit that was already a target in the earlier "
                                             "27-pair round is not presented as a new target.",
        "coverage_denominator": "Verdict coverage is |judged and determinate| / |determinate|, "
                                "never one minus the abstention rate.",
        "no_favourable_reference_choice": "The reference has a single main version. A sensitivity "
                                          "variant may be reported alongside it but never "
                                          "substituted for it on the basis of score.",
    }

    record = {
        "schema": "maf_kg.www_supplement.e2_target_selection.v1",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md",
        "no_model_calls": True,
        "frozen_inputs": {
            "catalog": "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json",
            "ku": "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/ku.json",
            "graph": "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/graph.json",
            "catalogue_size": len(catalog["knowledge_units"]),
            "reference": "evaluation/paper_closeout_2026-10-10/final_closeout_v1/"
                         "prerequisite_adjudication/reference_v3.json",
        },
        "projection": {
            "script": "scripts/build_ku_depends_candidates.py",
            "output": "evaluation/www_evidence_supplement_2026-10-11/e2_prerequisite/"
                      "projection_candidates_frozen.json",
            "total_candidates": len(proj["candidates"]),
            "foundation_guard": {
                "guard_names": ["映射", "函数的基本概念"],
                "present_in_catalogue": [
                    n for n in ("映射", "函数的基本概念") if n in set(names.values())
                ],
                "fires_on_this_catalogue": any(
                    n in set(names.values()) for n in ("映射", "函数的基本概念")
                ),
                "note": "The legacy foundation guard and the foundation-to-foundation "
                        "whitelist are inert on this catalogue, so the projection arm here "
                        "is the general algorithm, not a hand-written special case.",
            },
        },
        "targets": [
            {
                "unit_id": uid,
                "name": names.get(uid, ""),
                "chapter": chapters.get(uid, ""),
                "source": "plan" if uid in PLAN_TARGETS else "author_approved_expansion",
                "was_earlier_round_target": uid in EARLIER_ROUND_TARGETS,
                "projection_candidates_out": len(proj_by_source.get(uid, [])),
                "projection_targets": [
                    {"target_ku": c["target_ku"], "target_name": c["target_name"],
                     "signals": [s["type"] for s in c["signals"]],
                     "chain_total": c["chain_total"]}
                    for c in proj_by_source.get(uid, [])
                ],
                "reference_pairs_in_v3": ref_pairs_by_target.get(uid, 0),
                "expansion_why": next(
                    (a["why"] for a in SELECTED_ADDITIONS if a["unit_id"] == uid), None
                ),
            }
            for uid in selected
        ],
        "excluded_from_expansion": [
            {
                "unit_id": uid,
                "name": names.get(uid, ""),
                "reason": "already used as a target in the earlier 27-pair round",
            }
            for uid in EARLIER_ROUND_TARGETS
            if uid in names
        ],
        "pre_registered_rules": rules,
    }

    E2.mkdir(parents=True, exist_ok=True)
    (E2 / "target_selection.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # --- markdown ---------------------------------------------------------
    lines = [
        "# E2 先修目标集（S0.4 预注册）",
        "",
        "本文件在生成任何本轮预测之前冻结。参考的每一对都在看到本轮输出之前裁决；",
        "看到输出之后不增删、不改标签。",
        "",
        f"目录规模：{len(catalog['knowledge_units'])} 个 KU。"
        f"确定性投影（`scripts/build_ku_depends_candidates.py`）在本目录上产出 "
        f"{len(proj['candidates'])} 条候选。",
        "",
        "旧版 foundation 特判（`映射` / `函数的基本概念`）在本目录中不存在，",
        "因此本轮的投影臂是通用算法，不含手写特判。",
        "",
        "## 目标集（9 个）",
        "",
        "| # | 章 | KU | 来源 | 投影产出 | 参考对数 | 说明 |",
        "| ---: | --- | --- | --- | ---: | ---: | --- |",
    ]
    for i, t in enumerate(record["targets"], 1):
        src = "计划原定" if t["source"] == "plan" else "作者批准扩展"
        lines.append(
            f"| {i} | {t['chapter']} | {t['name']} | {src} | "
            f"{t['projection_candidates_out']} | {t['reference_pairs_in_v3']} | "
            f"{(t['expansion_why'] or '')[:40]} |"
        )
    lines += [
        "",
        "## 为什么扩展",
        "",
        "计划原定的 4 个目标只产生 5 条投影候选，其中「原函数与不定积分的概念」产出为 0 ——",
        "它在图里只作为被指向方出现，从不作为起点。这意味着该目标的全部参考对只能由语义提议器",
        "找回，投影臂对它没有任何信号。这不是错误，但 4 个目标不足以让投影臂的召回有意义。",
        "",
        "扩展后投影产出合计 "
        f"{sum(t['projection_candidates_out'] for t in record['targets'])} 条。",
        "",
        "选目标的三个依据：投影产出量、主题分散度、是否处在推导密集簇中。",
        "第三章的罗尔定理 → 拉格朗日中值定理 → 泰勒公式构成一条 `derived_from` 链：",
        "推导依据不等于应用型目标的先修，这正是论文要检验的混淆点，",
        "所以这一簇的目标密度是有意为之，不是为了平衡章节数而抹平。",
        "",
        "## 不选哪些",
        "",
        "旧一轮 27 对参考已经用过的 4 个目标不再作为新目标使用：",
        "",
    ]
    for e in record["excluded_from_expansion"]:
        lines.append(f"- {e['name']}（{e['unit_id']}）：{e['reason']}")
    lines += [
        "",
        "## 预注册的处理规则",
        "",
    ]
    for k, v in rules.items():
        lines.append(f"- **{k}**：{v}")
    lines.append("")

    (E2 / "target_selection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"catalogue          : {len(catalog['knowledge_units'])} KUs")
    print(f"projection total   : {len(proj['candidates'])} candidates")
    print(f"targets selected   : {len(record['targets'])}")
    for t in record["targets"]:
        print(f"  {t['chapter']:4s} {t['unit_id']:16s} proj={t['projection_candidates_out']:2d} "
              f"refpairs={t['reference_pairs_in_v3']:2d}  {t['name']}")
    print(f"projection out sum : {sum(t['projection_candidates_out'] for t in record['targets'])}")
    print(f"\nwrote {(E2 / 'target_selection.json').relative_to(WORKSPACE)}")
    print(f"wrote {(E2 / 'target_selection.md').relative_to(WORKSPACE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
