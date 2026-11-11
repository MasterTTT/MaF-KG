#!/usr/bin/env python
"""S1 §4.3/§4.4: build the adjudicated prerequisite reference (v3) and its diff.

Inputs are the four per-target proposal files produced by the review agents plus an explicit
override table, so that every change from the frozen v2 reference is attributable to one of:
  user_approved_existing      -- a decision the author had already approved
  assistant_source_adjudication -- a new source-based adjudication made in this round
  user_adjudicated_new        -- only if the author has answered; none in this round

The original reference_v2.json is never modified.

Usage:
    python analysis/final_closeout_s1_reference_v3.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADJ_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication"
PROPOSAL_DIR = ADJ_DIR / "proposals"
CONFIRM = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation"
V2 = CONFIRM / "reference_v2.json"
V3 = ADJ_DIR / "reference_v3.json"
SCOPE_CONTRACT = ADJ_DIR / "scope_contract.json"

# Lead decisions that take precedence over an agent proposal, with the reason recorded.
# Keep this table short and justified; every entry is disclosed in the report.
OVERRIDES: dict[str, dict] = {
    "N04": {
        "role": "uncertain",
        "directness": None,
        "provenance": "assistant_source_adjudication",
        "reason": (
            "验收方 R5：required/direct 依赖把目标范围从目录 description（建立定义与记号、理解"
            "「连续函数必有原函数」的结论、原函数族相差常数的结构）放宽到「能判断给定函数是否为原函数」。"
            "教材第四章 L22 是定义之后的「又如」复合函数求导说明、L109 是例2 求不定积分的计算，"
            "都不是原函数定义本身；放宽范围属事后澄清，不能当作模型当初收到的任务约束。"
            "按冻结规则记为未决，由作者裁决；敏感性见 reference_v3_n04_required.json。"
        ),
    },
}

# Pairs whose frozen v2 reason is wrong about *where* the dependency is used, even when the role
# itself survives.  The corrected reason is carried into v3 so the record is not self-contradictory.
REASON_OVERRIDES: dict[str, str] = {
    "N02": (
        "目标自身在 第四章 L42 通过 [F(x)+C]′=f(x) 说明原函数族相差常数，L60 又用 [Φ−F]′=Φ′−F′；"
        "这些是目标正文里的实际求导运算（和、差、常数倍），因此是和差积商求导法则的直接使用。"
        "原理由提到的“不定积分线性性质”属于另一个 KU（ku_294ed87693ea），不能用来支撑本对。"
    ),
}

# Provenance per pair; default is an assistant source adjudication.  Only pairs the author had
# already decided keep a different provenance.
PROVENANCE_BY_PAIR = {"N15": "user_approved_existing"}

# The author's answer to the S1 review, received 2026-10-10 after the closeout report:
#   "N04 选 A（未决）；其余九对接受".
# From this point the changed pairs carry user_adjudicated_new provenance rather than an assistant
# adjudication.  N15 keeps user_approved_existing because that decision predates this round.
AUTHOR_DECISION = {
    "decided_at": "2026-10-10",
    "decided_by": "author",
    "verbatim": "首先，选A吧 其余九对接受",
    "n04_option": "A",
    "n04_meaning": (
        "目标范围按目录 description：建立定义与记号、理解存在定理的结论、原函数族相差常数的结构；"
        "不包含「能判断给定函数是否为原函数」。因此 N04 记为 uncertain（未决），不进任何分母。"
    ),
    "accepted_pairs": ["N01", "N04", "N09", "N10", "N19", "N20", "N23", "N25", "N27"],
    "kept_prior_approval": ["N15"],
    "effect_on_scores": (
        "无：选项 A 就是主参考 reference_v3.json 的既有状态，因此全部数字与裁决前一致。"
        "敏感性变体 reference_v3_n04_required.json 保留，仅作敏感性，不再作为候选主参考。"
    ),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    v2 = read_json(V2)
    pairs_v2 = v2["pairs"]
    by_id = {pair["id"]: pair for pair in pairs_v2}

    proposals = {}
    for path in sorted(PROPOSAL_DIR.glob("agent_*.json")):
        data = read_json(path)
        for verdict in data["verdicts"]:
            if verdict["id"] in proposals:
                raise SystemExit(f"duplicate verdict for {verdict['id']} in {path.name}")
            verdict["_proposal_file"] = path.name
            proposals[verdict["id"]] = verdict

    missing = sorted(set(by_id) - set(proposals))
    if missing:
        raise SystemExit(f"no proposal for: {missing}")

    pairs_v3 = []
    adjudications = []
    for pair in pairs_v2:
        pid = pair["id"]
        proposal = proposals[pid]
        override = OVERRIDES.get(pid, {})
        role = override.get("role", proposal["proposed_role"])
        directness = override.get("directness", proposal.get("proposed_directness"))
        if role == "required":
            if directness not in {"direct", "indirect"}:
                raise SystemExit(f"{pid}: required needs directness, got {directness!r}")
        else:
            directness = None

        changed = role != pair["role"] or directness != pair.get("directness")
        if role == "required":
            denominator_effect = "in_positive_denominator"
        elif pair["role"] == "required":
            denominator_effect = "removed_from_positive_denominator"
        elif role == "uncertain":
            denominator_effect = "excluded_from_both_denominators"
        else:
            denominator_effect = "unchanged_negative"

        provenance = override.get(
            "provenance",
            PROVENANCE_BY_PAIR.get(pid, "assistant_source_adjudication"),
        )
        author_confirmed = pid in AUTHOR_DECISION["accepted_pairs"]
        if author_confirmed and pid not in PROVENANCE_BY_PAIR:
            provenance = "user_adjudicated_new"

        new_pair = dict(pair)
        new_pair["role"] = role
        new_pair["directness"] = directness
        new_pair["provenance"] = provenance
        new_pair["v2_role"] = pair["role"]
        new_pair["v2_directness"] = pair.get("directness")
        new_pair["v2_reason"] = pair.get("reason")
        new_pair["adjudication_reason"] = REASON_OVERRIDES.get(pid, proposal.get("reason"))
        new_pair["scope_basis"] = proposal.get("scope_basis")
        new_pair["proposal_file"] = proposal["_proposal_file"]
        new_pair["proposal_confidence"] = proposal.get("confidence")
        new_pair["changed_in_v3"] = changed
        if author_confirmed:
            new_pair["author_confirmation"] = {
                "status": "author_accepted",
                "decided_at": AUTHOR_DECISION["decided_at"],
                "decision": AUTHOR_DECISION["verbatim"],
                "note": AUTHOR_DECISION["n04_meaning"] if pid == "N04" else None,
            }
        if override:
            new_pair["lead_override"] = override
        pairs_v3.append(new_pair)

        adjudications.append(
            {
                "id": pid,
                "target_ku": pair["target_ku"],
                "target_name": pair["target_name"],
                "prerequisite_ku": pair["prerequisite_ku"],
                "prerequisite_name": pair["prerequisite_name"],
                "target_scope": None,  # filled below from the scope contract
                "v2_role": pair["role"],
                "v2_directness": pair.get("directness"),
                "v2_reason": pair.get("reason"),
                "v3_role": role,
                "v3_directness": directness,
                "changed": changed,
                "denominator_effect": denominator_effect,
                "adjudication_source": provenance,
                "author_confirmed": author_confirmed,
                "author_decision": AUTHOR_DECISION["verbatim"] if author_confirmed else None,
                "reason": REASON_OVERRIDES.get(pid, proposal.get("reason")),
                "proposal_reason": proposal.get("reason"),
                "reason_corrected_in_v3": pid in REASON_OVERRIDES,
                "scope_basis": proposal.get("scope_basis"),
                "source_evidence": proposal.get("source_evidence", []),
                "confidence": proposal.get("confidence"),
                "proposal_file": proposal["_proposal_file"],
                "lead_override": override or None,
            }
        )

    scope = read_json(SCOPE_CONTRACT) if SCOPE_CONTRACT.is_file() else {"new_targets": []}
    scope_by_target = {t["unit_id"]: t.get("adopted_scope") for t in scope.get("new_targets", [])}
    for row in adjudications:
        row["target_scope"] = scope_by_target.get(row["target_ku"])

    role_counts: dict = {}
    for pair in pairs_v3:
        key = pair["role"] if pair["role"] != "required" else f"required_{pair['directness']}"
        role_counts[key] = role_counts.get(key, 0) + 1

    v3 = {
        "experiment": "prerequisite-new-target-confirmation-v3",
        "status": "reference_adjudicated_after_prediction_responses_were_frozen",
        "supersedes": "reference_v2.json (never modified)",
        "adjudication_basis": (
            "S1 of docs/DSH_FINAL_EXPERIMENT_CLOSEOUT_PLAN_2026-10-10.md. The model responses were "
            "already frozen; only the reference roles were re-adjudicated, on the source text and on "
            "the review instruction that was actually sent."
        ),
        "review_instruction_applied": (
            "本次目标统一为理解与应用生成KU描述中的数学内容，不要求事先掌握该内容的完整证明。"
        ),
        "targets": v2["targets"],
        "pairs": pairs_v3,
        "counts": {
            "pairs": len(pairs_v3),
            "by_role": role_counts,
            "changed_pairs": sum(1 for pair in pairs_v3 if pair["changed_in_v3"]),
        },
        "role_coverage_correction": (
            "reference_v2 claimed that no proof_support or principle_support pair could be assigned "
            "inside this catalogue. That claim was wrong: the proof component for the "
            "Newton-Leibniz target (ku_f10444319956) is in the catalogue and is the same unit the "
            "older 27-pair reference already scored as proof_support, and the derivation source of "
            "the integration-by-parts formula is the product rule, which the author had already "
            "approved as principle_support."
        ),
        "provenance_note": (
            "N15 keeps user_approved_existing (that decision predates this round). The nine pairs the "
            "author accepted on 2026-10-10, including N04 as uncertain, carry user_adjudicated_new. "
            "Nothing is labelled user_adjudicated_new without an actual author answer."
        ),
        "author_decision": AUTHOR_DECISION,
        "reference_v2_sha256": sha256_file(V2),
    }
    V3.write_text(json.dumps(v3, ensure_ascii=False, indent=2), encoding="utf-8")

    # Sensitivity variant: N04 kept at the proposal's required/direct instead of uncertain, so the
    # author's scope decision can be read off directly.  Nothing else differs.
    sensitivity = json.loads(json.dumps(v3))
    sensitivity["experiment"] = "prerequisite-new-target-confirmation-v3-n04-required"
    sensitivity["sensitivity_note"] = (
        "Identical to reference_v3.json except that N04 is kept at required/direct, which is the "
        "reading that requires the target scope to include 'judging whether a given function is an "
        "antiderivative'. Reported next to the main reference, not instead of it."
    )
    for pair in sensitivity["pairs"]:
        if pair["id"] != "N04":
            continue
        pair["role"] = "required"
        pair["directness"] = "direct"
        pair["changed_in_v3"] = True
        pair["lead_override"] = {"role": "required", "directness": "direct", "sensitivity_variant": True}
    sensitivity_counts: dict = {}
    for pair in sensitivity["pairs"]:
        key = pair["role"] if pair["role"] != "required" else f"required_{pair['directness']}"
        sensitivity_counts[key] = sensitivity_counts.get(key, 0) + 1
    sensitivity["counts"] = {
        "pairs": len(sensitivity["pairs"]),
        "by_role": sensitivity_counts,
        "changed_pairs": sum(1 for pair in sensitivity["pairs"] if pair["changed_in_v3"]),
    }
    (ADJ_DIR / "reference_v3_n04_required.json").write_text(
        json.dumps(sensitivity, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    diff = {
        "schema": "dsh.final_closeout.s1_reference_diff.v1",
        "from": V2.relative_to(REPO).as_posix(),
        "from_sha256": sha256_file(V2),
        "to": V3.relative_to(REPO).as_posix(),
        "to_sha256": sha256_file(V3),
        "changed": [
            {
                "id": row["id"],
                "pair": f"{row['target_name']} -> {row['prerequisite_name']}",
                "v2": f"{row['v2_role']}/{row['v2_directness']}",
                "v3": f"{row['v3_role']}/{row['v3_directness']}",
                "denominator_effect": row["denominator_effect"],
                "adjudication_source": row["adjudication_source"],
                "reason": row["reason"],
            }
            for row in adjudications
            if row["changed"]
        ],
        "unchanged": [row["id"] for row in adjudications if not row["changed"]],
    }
    (ADJ_DIR / "reference_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    adjudication_doc = {
        "schema": "dsh.final_closeout.s1_adjudications.v1",
        "purpose": (
            "Per-pair record of the reference revision: original label, new label, target scope, "
            "textbook source path and line numbers, excerpt, reason, adjudication source, and whether "
            "the change moves the pair in or out of the recall denominator."
        ),
        "reference_v2": V2.relative_to(REPO).as_posix(),
        "reference_v2_sha256": sha256_file(V2),
        "reference_v3": V3.relative_to(REPO).as_posix(),
        "reference_v3_sha256": sha256_file(V3),
        "counts": v3["counts"],
        "items": adjudications,
    }
    (ADJ_DIR / "adjudications.json").write_text(
        json.dumps(adjudication_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Standalone record of the author's decision, so the provenance change is auditable on its own.
    author_doc = {
        "schema": "dsh.final_closeout.s1_author_decision.v1",
        "decided_at": AUTHOR_DECISION["decided_at"],
        "decided_by": AUTHOR_DECISION["decided_by"],
        "author_statement_verbatim": AUTHOR_DECISION["verbatim"],
        "scope_question": (
            "N04（原函数与不定积分的概念 → 复合函数的求导法则）是否按「目标包含能判断给定函数是否为"
            "原函数」来解释。"
        ),
        "options": {
            "A": AUTHOR_DECISION["n04_meaning"] + " 本轮主参考。",
            "B": (
                "目标包含「能判断给定函数是否为原函数」，N04 记 required/direct。"
                "见 reference_v3_n04_required.json，仅作敏感性。"
            ),
        },
        "decision": "A",
        "accepted_pairs": AUTHOR_DECISION["accepted_pairs"],
        "kept_prior_approval": AUTHOR_DECISION["kept_prior_approval"],
        "effect_on_scores": AUTHOR_DECISION["effect_on_scores"],
        "reference_v3": V3.relative_to(REPO).as_posix(),
        "reference_v3_sha256": sha256_file(V3),
        "sensitivity_variant": (ADJ_DIR / "reference_v3_n04_required.json").relative_to(REPO).as_posix(),
    }
    (ADJ_DIR / "author_decision.json").write_text(
        json.dumps(author_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"wrote {V3.relative_to(REPO).as_posix()}")
    print(f"  counts: {json.dumps(v3['counts'], ensure_ascii=False)}")
    print(f"wrote {(ADJ_DIR / 'reference_diff.json').relative_to(REPO).as_posix()}")
    for row in diff["changed"]:
        print(f"  {row['id']:4s} {row['v2']:22s} -> {row['v3']:22s} {row['denominator_effect']}")
    print(f"wrote {(ADJ_DIR / 'adjudications.json').relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
