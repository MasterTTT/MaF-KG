"""S0.6 build the pre-spend budget plan for the WWW evidence supplement.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 7.1.

Estimates are scaled from the recorded ledger cost of the earlier merged
comparison. They are order-of-magnitude planning numbers at the historical
ledger rate, not a promise about Go subscription billing. No model call is made
here.

Outputs:
  evaluation/www_evidence_supplement_2026-10-11/budgets/budget_plan.json
  evaluation/www_evidence_supplement_2026-10-11/budgets/budget_plan.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
ROOT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
BUDGETS = ROOT / "budgets"
FOUR_ARM = WORKSPACE / "evaluation/external_baselines_merged_2026-10-09/four_arm_table.json"
SOURCE_REPO = Path("E:/MaF-KG")

# Reference size of the earlier merged input, in the same convention the plan uses
# for the new inputs (decoded characters including LF separators).
EARLIER_INPUT_CHARS = 32853

NEW_INPUTS = {
    "I": {"file": "第四章_全章_无习题.md", "lo": 5, "hi": 1126},
    "O": {"file": "第七章_全章_无习题.md", "lo": 973, "hi": 1809},
}
PREFIXES = {
    "I1": {"input": "I", "lo": 5, "hi": 375},
    "I2": {"input": "I", "lo": 5, "hi": 947},
    "I3": {"input": "I", "lo": 5, "hi": 1126, "reuses_e1_run": True},
}

# Runs per method for the main comparison: 2 inputs x 2 repeats.
E1_REPEATS = 2
PREFIX_REPEATS = 2
E3_REPEATS = 2

# Assumed per-review-call cost is derived from the earlier review ledgers if
# available; otherwise a conservative flat allowance per reviewed output.
FALLBACK_REVIEW_TOKENS_PER_OUTPUT = 120_000


def slice_chars(name: str, lo: int, hi: int) -> int:
    path = SOURCE_REPO / "data/raw" / name
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    seg = "\n".join(lines[lo - 1 : hi])
    return len(seg)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    four = json.loads(FOUR_ARM.read_text(encoding="utf-8"))
    per_run: dict[str, dict] = {}
    for arm in four["arms"]:
        reps = arm["reviewed_repeats"] or 1
        per_run[arm["arm"]] = {
            "tokens_per_run": arm["spend"]["tokens"] / reps,
            "usd_per_run": arm["spend"]["usd"] / reps,
            "requests_per_run": arm["spend"]["requests"] / reps,
        }

    sizes = {k: slice_chars(v["file"], v["lo"], v["hi"]) for k, v in NEW_INPUTS.items()}
    prefix_sizes = {k: slice_chars(NEW_INPUTS[v["input"]]["file"], v["lo"], v["hi"])
                    for k, v in PREFIXES.items()}
    scale = {k: v / EARLIER_INPUT_CHARS for k, v in sizes.items()}
    prefix_scale = {k: v / EARLIER_INPUT_CHARS for k, v in prefix_sizes.items()}

    # Measured chunk counts from the zero-cost S0 dry run of the frozen chunker.
    # MaF cost tracks chunk count (one request per chunk with growing memory),
    # while Direct/EDC/iText2KG track text volume. Use the matching scale for each.
    EARLIER_CHUNKS = 32
    MEASURED_CHUNKS = {"I": 35, "O": 13}
    chunk_scale = {k: v / EARLIER_CHUNKS for k, v in MEASURED_CHUNKS.items()}

    arms = list(per_run)

    def cost(arm: str, s: float) -> tuple[float, float]:
        return per_run[arm]["tokens_per_run"] * s, per_run[arm]["usd_per_run"] * s

    def arm_scale(arm: str, inp: str) -> float:
        return chunk_scale[inp] if arm == "maf" else scale[inp]

    tasks: list[dict] = []

    # --- E1: main comparison -------------------------------------------------
    e1_tok = e1_usd = 0.0
    detail_e1 = []
    for inp in ("I", "O"):
        for arm in arms:
            t, u = cost(arm, arm_scale(arm, inp))
            t *= E1_REPEATS
            u *= E1_REPEATS
            e1_tok += t
            e1_usd += u
            detail_e1.append({"input": inp, "arm": arm, "runs": E1_REPEATS,
                              "scale": round(arm_scale(arm, inp), 3),
                              "est_tokens": round(t), "est_usd": round(u, 4)})
    tasks.append({
        "id": "E1", "label": "主比较：两份新输入 × 四方法 × 两次",
        "method_runs": 2 * len(arms) * E1_REPEATS,
        "est_tokens": round(e1_tok), "est_usd": round(e1_usd, 2),
        "detail": detail_e1,
    })

    # --- E1p: independent prefixes (I3 reuses the E1 runs) -------------------
    e1p_tok = e1p_usd = 0.0
    detail_e1p = []
    for pfx in ("I1", "I2"):
        for arm in ("direct", "maf"):
            t, u = cost(arm, prefix_scale[pfx])
            t *= PREFIX_REPEATS
            u *= PREFIX_REPEATS
            e1p_tok += t
            e1p_usd += u
            detail_e1p.append({"prefix": pfx, "arm": arm, "runs": PREFIX_REPEATS,
                               "est_tokens": round(t), "est_usd": round(u, 4)})
    tasks.append({
        "id": "E1p", "label": "独立前缀：I1/I2 × Direct/MaF × 两次（I3 复用 E1）",
        "method_runs": 2 * 2 * PREFIX_REPEATS,
        "est_tokens": round(e1p_tok), "est_usd": round(e1p_usd, 2),
        "detail": detail_e1p,
    })

    # --- E2: prerequisite module --------------------------------------------
    # Two arms x two repeats over 9 targets and a frozen 28-pair reference.
    # Proposer and reviewer calls are small and batched; scale from the recorded
    # new-target review ledger is not available for the full module, so this is a
    # conservative flat allowance per module run.
    # CORRECTED 2026-10-11 from the E2 runner's own measured bound. The original
    # flat allowance (60k tokens / $0.03 per run) was wrong by an order of
    # magnitude: the reviewer sees the whole candidate block per batch, so each
    # run costs far more. Measured on the frozen reference: 3,876,172 tokens and
    # USD 1.5138 for the four runs, i.e. ~969k tokens / ~USD 0.378 per run.
    e2_per_run_tokens = 969_043
    e2_per_run_usd = 0.3785
    e2_runs = 2 * 2
    tasks.append({
        "id": "E2",
        "label": "先修完整模块：投影臂与投影+补候选臂，各两次（实测修正）",
        "estimate_source": "measured bound from run_supplement_e2.py prepare, not a flat allowance",
        "method_runs": e2_runs,
        "est_tokens": e2_per_run_tokens * e2_runs,
        "est_usd": round(e2_per_run_usd * e2_runs, 2),
        "detail": [{"arm": a, "runs": 2, "est_tokens": e2_per_run_tokens * 2,
                    "est_usd": round(e2_per_run_usd * 2, 4)}
                   for a in ("P_projection", "P+S_full_module")],
    })

    # --- E3: memory ablation (mandatory, author decision 2026-10-11) --------
    e3_tok = e3_usd = 0.0
    detail_e3 = []
    for inp in ("I", "O"):
        t, u = cost("maf", chunk_scale[inp])
        t *= E3_REPEATS
        u *= E3_REPEATS
        e3_tok += t
        e3_usd += u
        detail_e3.append({"input": inp, "arm": "maf_no_visible_memory", "runs": E3_REPEATS,
                          "est_tokens": round(t), "est_usd": round(u, 4)})
    tasks.append({
        "id": "E3", "label": "记忆消融（作者已升为必做）：两输入各两次无可见记忆顺序运行",
        "method_runs": 2 * E3_REPEATS,
        "est_tokens": round(e3_tok), "est_usd": round(e3_usd, 2),
        "detail": detail_e3,
    })

    # --- reviews -------------------------------------------------------------
    e3_runs = 2 * E3_REPEATS
    reviewed_outputs = (2 * len(arms) * E1_REPEATS) + (2 * 2 * PREFIX_REPEATS) + e3_runs
    review_tokens = FALLBACK_REVIEW_TOKENS_PER_OUTPUT * reviewed_outputs
    review_usd = review_tokens * (per_run["direct"]["usd_per_run"] / per_run["direct"]["tokens_per_run"])
    tasks.append({
        "id": "REVIEW", "label": "方法标签盲化的源文评审 + 作者核查包 + 参考整理辅助",
        "reviewed_outputs": reviewed_outputs,
        "est_tokens": round(review_tokens), "est_usd": round(review_usd, 2),
    })

    total_tok = sum(t["est_tokens"] for t in tasks)
    total_usd = sum(t["est_usd"] for t in tasks)

    plan = {
        "schema": "maf_kg.www_supplement.budget_plan.v1",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md",
        "no_model_calls_made": True,
        "rate_basis": "historical ledger rate of the earlier merged comparison; "
                      "not a claim about Go subscription billing",
        "reference_input_chars": EARLIER_INPUT_CHARS,
        "new_input_chars": sizes,
        "prefix_chars": prefix_sizes,
        "scale_factors": {k: round(v, 3) for k, v in scale.items()},
        "prefix_scale_factors": {k: round(v, 3) for k, v in prefix_scale.items()},
        "measured_chunk_counts": MEASURED_CHUNKS,
        "chunk_scale_factors": {k: round(v, 3) for k, v in chunk_scale.items()},
        "scaling_rule": "MaF is scaled by measured chunk count (one request per chunk with "
                        "growing memory); Direct, EDC and iText2KG are scaled by text volume.",
        "historical_per_run": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv
                                   for kk, vv in v.items()} for k, v in per_run.items()},
        "tasks": tasks,
        "totals": {
            "method_runs": sum(t.get("method_runs", 0) for t in tasks),
            "est_tokens": total_tok,
            "est_usd": round(total_usd, 2),
        },
        "caps": {
            "stage_token_cap": round(total_tok * 1.25),
            "stage_usd_cap": round(total_usd * 1.25, 2),
            "max_concurrent_research_requests": 2,
            "hard_stop_on": [
                "input or reference hash change",
                "gold or adjudication entering a model payload",
                "wrong adapter version imported",
                "budget, call or deadline limit reached",
                "consecutive no-progress",
                "model or provider change",
            ],
        },
        "notes": [
            "Run counts are not call counts: EDC and iText2KG issue many calls per run.",
            "The EDC arm dominates the estimate; if budget is tight, dropping an EDC or "
            "iText2KG repeat costs far less evidence than dropping any E1 arm.",
            "Review cost is a flat allowance per reviewed output and is the least certain line.",
            "Failures keep their ledger entries; the cap already includes headroom.",
        ],
        "author_authorization_required": True,
    }

    BUDGETS.mkdir(parents=True, exist_ok=True)
    (BUDGETS / "budget_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# S0.6 预算计划（付费前）",
        "",
        "本文件在任何付费调用之前产出。费率基准是旧合并对照的账本费率，",
        "不是 Go 订阅实际扣费金额的承诺。",
        "",
        f"参照输入：{EARLIER_INPUT_CHARS:,} 字符 / {EARLIER_CHUNKS} 个顺序分块。"
        f"新输入 I：{sizes['I']:,} 字符 / {MEASURED_CHUNKS['I']} 块"
        f"（字符×{scale['I']:.2f}，分块×{chunk_scale['I']:.2f}）；"
        f"O：{sizes['O']:,} 字符 / {MEASURED_CHUNKS['O']} 块"
        f"（字符×{scale['O']:.2f}，分块×{chunk_scale['O']:.2f}）。",
        "",
        "分块数为 S0 干跑冻结分块器实测所得，不是估计值。MaF 按分块数缩放"
        "（每块一次请求，记忆随图增长），其余三方法按文本量缩放。",
        "",
        "| 任务 | 方法运行 | 预估 token | 预估 USD |",
        "| --- | ---: | ---: | ---: |",
    ]
    for t in tasks:
        lines.append(f"| {t['label']} | {t.get('method_runs', '-')} | "
                     f"{t['est_tokens']:,} | {t['est_usd']:.2f} |")
    lines += [
        f"| **合计** | **{plan['totals']['method_runs']}** | "
        f"**{total_tok:,}** | **{total_usd:.2f}** |",
        "",
        f"阶段上限：{plan['caps']['stage_token_cap']:,} token / "
        f"${plan['caps']['stage_usd_cap']:.2f}（含 25% 余量）。",
        "",
        "## 说明",
        "",
    ]
    for n in plan["notes"]:
        lines.append(f"- {n}")
    lines += [
        "",
        "## 硬停止条件",
        "",
    ]
    for c in plan["caps"]["hard_stop_on"]:
        lines.append(f"- {c}")
    lines.append("")

    (BUDGETS / "budget_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"new input chars   : I={sizes['I']:,} (x{scale['I']:.2f})  O={sizes['O']:,} (x{scale['O']:.2f})")
    print(f"prefix chars      : " + "  ".join(f"{k}={v:,} (x{prefix_scale[k]:.2f})" for k, v in prefix_sizes.items()))
    for t in tasks:
        print(f"  {t['id']:7s} runs={str(t.get('method_runs','-')):>3s} "
              f"tok={t['est_tokens']:>11,}  usd={t['est_usd']:>6.2f}   {t['label'][:52]}")
    print(f"  {'TOTAL':7s} runs={plan['totals']['method_runs']:>3d} "
          f"tok={total_tok:>11,}  usd={total_usd:>6.2f}")
    print(f"\nwrote {(BUDGETS / 'budget_plan.json').relative_to(WORKSPACE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
