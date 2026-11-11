#!/usr/bin/env python
"""S1 §4.1: build the per-target scope contract.

The point of this file is to record, for every target, what the model was actually asked to do
(the frozen review system prompt), what the selection metadata claims, and which scope the
adjudication adopts.  Where those disagree, the disagreement is recorded rather than papered over.

Read-only with respect to the experiments.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/.

Usage:
    python analysis/final_closeout_s1_scope_contract.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication"

CONFIRM_DIR = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation"
SELECTION = CONFIRM_DIR / "selection.json"
NEW_REF = CONFIRM_DIR / "reference_v2.json"
REVIEW_SYSTEM = (
    REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/snapshot/review_system.txt"
)
OLD_REF = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/reference.json"
CATALOG = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json"
ARM_KU_BATCH = CONFIRM_DIR / "arm_ku/requests/batch_1.json"
ARM_CTX_BATCH = CONFIRM_DIR / "arm_ctx/requests/batch_1.json"

# What the frozen system prompt actually tells the reviewer.  Quoted, not paraphrased.
REVIEW_INSTRUCTION = (
    "本次目标统一为理解与应用生成KU描述中的数学内容，不要求事先掌握该内容的完整证明。"
)

ADOPTED_SCOPE = {
    "ku_fb8130a0dde2": (
        "按目录 description 的范围：建立原函数与不定积分的定义与记号，理解「连续函数必有原函数」这一结论，"
        "以及原函数族相差常数的结构。**不**把「能判断给定函数是否为原函数」纳入本轮采用范围——"
        "该表述不在目录 description 里，属事后放宽，见下面的 conflict 记录。"
    ),
    "ku_da56c3fdccf2": (
        "理解罗尔定理的条件与结论、几何意义，并能在具体函数上验证条件、使用结论；"
        "不要求独立复现定理的证明。"
    ),
    "ku_e9f8c9c2740e": (
        "理解分部积分公式与 u/dv 选取原则，并能对目录描述列举的常见乘积类型求不定积分；"
        "不要求独立推导该公式。"
    ),
    "ku_99bdf15cb98e": (
        "理解微积分基本定理的内容，并能利用原函数计算定积分；不要求独立证明该公式。"
    ),
}

CONFLICTS = {
    "ku_fb8130a0dde2": {
        "has_conflict": True,
        "conflict": (
            "验收方 R5（2026-10-10）：N04（原函数与不定积分的概念 → 复合函数的求导法则）被判 required/direct，"
            "依据是第四章 L22 与 L109。但 L22 是定义之后的「又如」复合函数求导说明，L109 是例2 求不定积分的计算，"
            "二者都不是原函数定义本身；把「能判断给定函数是否为原函数」纳入采用范围是事后放宽。"
        ),
        "resolution": (
            "本轮采用**目录 description 的范围**（建立定义与记号、理解存在定理结论与族结构），"
            "不把该表述当成本轮范围；N04 因此记为 `uncertain`（未决），由作者裁决。"
            "敏感性结果见 `reference_v3_n04_required.json`（该变体保留 N04 = required/direct）。"
            "scope_contract 是事后审阅澄清，**不得**被表述为模型当初收到的任务约束。"
        ),
    },
    "ku_da56c3fdccf2": {
        "has_conflict": True,
        "conflict": (
            "selection 与 reference_v2 把该目标记为 theorem_proof，但实际发出的审核指令是"
            "“理解与应用，不要求完整证明”。"
        ),
        "resolution": (
            "按实际发出的审核指令采用“理解与应用”范围；theorem_proof 这一标注保留在 selection 中作为历史记录，"
            "不得据此把证明用到的工具记成必需先修，也不得声称模型收到过“必须会证明”的目标。"
        ),
    },
    "ku_e9f8c9c2740e": {
        "has_conflict": False,
        "note": "goal_type=method_application 与“理解与应用”一致；公式推导来源按 principle_support 处理。",
    },
    "ku_99bdf15cb98e": {
        "has_conflict": True,
        "conflict": (
            "selection 记为 theorem_application（应用型），但 reference_v2 的 N23 理由写的是"
            "“没有这一单元就无法证明公式”，即按证明目标立论，与“不要求完整证明”的实际指令冲突。"
        ),
        "resolution": (
            "按实际指令采用“应用”范围；只用于证明公式的依赖记 proof_support。"
            "旧 27 对参考已对同类目标使用 proof_support（其 N03/N04），本次与该先例保持一致。"
        ),
    },
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    new_ref = json.loads(NEW_REF.read_text(encoding="utf-8"))
    old_ref = json.loads(OLD_REF.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    review_system = REVIEW_SYSTEM.read_text(encoding="utf-8")

    catalogue = {u.get("id"): u for u in catalog.get("knowledge_units", [])}
    sel_targets = {t["unit_id"]: t for t in selection.get("targets", [])}

    # Confirm from the actual payload what the reviewer was shown for each target.
    payload_seen = {}
    for label, path in (("ku", ARM_KU_BATCH), ("ctx", ARM_CTX_BATCH)):
        if not path.is_file():
            payload_seen[label] = {"available": False}
            continue
        body = json.loads(path.read_text(encoding="utf-8")).get("body", {})
        messages = body.get("messages", [])
        user = messages[1]["content"] if len(messages) > 1 else ""
        payload_seen[label] = {
            "available": True,
            "path": path.relative_to(REPO).as_posix(),
            "system_prompt_sha256": (
                hashlib.sha256(str(messages[0]["content"]).encode("utf-8")).hexdigest()
                if messages
                else None
            ),
            "user_payload_chars": len(user),
            "user_payload_carries": (
                "target name, chapter, catalogue description and card line spans"
                if "start_line" in user
                else "unknown"
            ),
            "user_payload_carries_card_text": "card_text" in user,
        }

    targets = []
    for target in new_ref.get("targets", []):
        unit_id = target["unit_id"]
        unit = catalogue.get(unit_id, {})
        sel = sel_targets.get(unit_id, {})
        targets.append(
            {
                "unit_id": unit_id,
                "name": target.get("name"),
                "catalogue_description": unit.get("description"),
                "catalogue_position": target.get("catalogue_position"),
                "goal_type_in_selection": sel.get("goal_type"),
                "goal_type_in_reference_v2": target.get("goal_type"),
                "selection_rationale": sel.get("why"),
                "actual_review_instruction": REVIEW_INSTRUCTION,
                "review_system_source": REVIEW_SYSTEM.relative_to(REPO).as_posix(),
                "review_system_sha256": sha256_file(REVIEW_SYSTEM),
                "adopted_scope": ADOPTED_SCOPE.get(unit_id),
                "scope_decision_basis": (
                    "实际发出的系统指令 + 目录 description；采用事后审阅修订口径，"
                    "不称预注册，也不称独立盲测。"
                ),
                **CONFLICTS.get(unit_id, {"has_conflict": False}),
            }
        )

    old_targets = []
    seen: dict = {}
    for ann in old_ref.get("annotations", []) + old_ref.get("bridges", []):
        seen.setdefault(ann.get("target_ku"), ann.get("target_name"))
    for target_ku, name in seen.items():
        unit = catalogue.get(target_ku, {})
        old_targets.append(
            {
                "target_ku": target_ku,
                "name": name,
                "catalogue_description": unit.get("description"),
                "goal_type_in_selection": None,
                "actual_review_instruction": REVIEW_INSTRUCTION,
                "adopted_scope": "同一条冻结指令；按各条 annotation 自带的 required_scope 执行。",
                "note": (
                    "旧 27 对由同一份 review_system 审核，因此同样按“理解与应用、不要求完整证明”解释。"
                    "其 N03/N04 已把积分上限函数及其导数记为 proof_support，是本次新参考 N23 的先例。"
                ),
            }
        )

    payload = {
        "schema": "dsh.final_closeout.s1_scope_contract.v1",
        "purpose": (
            "逐目标记录原描述、原 goal_type、实际审核指令、本次审阅采用的范围、冲突及处理依据。"
            "本文件是事后审阅修订，不是预注册，也不是独立盲测。"
        ),
        "review_instruction_verbatim": REVIEW_INSTRUCTION,
        "review_system_full_text": review_system,
        "review_system_source": REVIEW_SYSTEM.relative_to(REPO).as_posix(),
        "review_system_sha256": sha256_file(REVIEW_SYSTEM),
        "what_the_reviewer_actually_received": payload_seen,
        "new_targets": targets,
        "old_targets": old_targets,
        "rule_applied": {
            "required": "在已声明的学习目标范围内，不掌握该内容就不能理解或完成目标",
            "direct": "目标自身直接使用，中间没有其他教学目标承接",
            "indirect": "经另一个教学目标（KU）承接；证明中有若干推导步骤不算 indirect",
            "principle_support": "解释公式或方法为什么成立的原理来源",
            "proof_support": "用于证明目标结论；在不要求完整证明的范围下不自动成为必需",
            "related_or_none": "共同主题、后续应用、可替代工具、仅特定分支需要、方向相反",
            "uncertain": "教材与目标范围不足以确定；不计入正例也不计入负例",
        },
    }
    out = OUT_DIR / "scope_contract.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO).as_posix()}")
    print(f"  new targets={len(targets)} old targets={len(old_targets)}")
    print(f"  conflicts: {[t['unit_id'] for t in targets if t.get('has_conflict')]}")
    print(f"  payload_seen: {json.dumps(payload_seen, ensure_ascii=False)[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
