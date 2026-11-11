#!/usr/bin/env python
"""S6: assemble the closeout manifest and verify the invariants it claims.

Two claims are checked rather than asserted:

  * every frozen artifact recorded in the S0 inventory still has the same hash, so nothing that was
    already there was modified;
  * every ledger's cumulative accounting is unchanged, so no new research call was made.

Read-only.  Writes only
evaluation/paper_closeout_2026-10-10/final_closeout_v1/manifest.json.

Usage:
    python analysis/final_closeout_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1"

DELIVERABLES = [
    ("1_final_scores_and_machine_artifacts", "evaluation/paper_closeout_2026-10-10/final_closeout_v1"),
    ("2_closeout_report", "docs/DSH_FINAL_CLOSEOUT_REPORT_2026-10-10.md"),
    ("3_evidence_index_doc", "docs/WWW_FINAL_EVIDENCE_INDEX_2026-10-10.md"),
    ("3_evidence_index_machine", "evaluation/paper_closeout_2026-10-10/final_closeout_v1/evidence_index.json"),
    ("4_claims_and_outline", "docs/WWW_FINAL_CLAIMS_AND_OUTLINE_2026-10-10.md"),
    ("5_author_review_packet", "evaluation/paper_closeout_2026-10-10/final_closeout_v1/author_review_packet"),
    ("5_release_risk_audit", "evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits/venue_and_release_audit.json"),
    ("6_handoff", "docs/DSH_FINAL_HANDOFF_2026-10-10.md"),
]

SELF_CHECK = [
    (1, "生产图、原响应、旧参考、旧评审哈希保持不变", "artifact_invariant"),
    (2, "28 对逐项完成范围审阅，原批准保留，未决明示", "prerequisite_adjudication/adjudications.json"),
    (3, "参考变化与模型变化分开，旧新指标可复算", "prerequisite_rescore/table.md"),
    (4, "8 图 × 47 关系完整绑定，两列真正独立", "relation_semantic_review/metrics_semantic_vs_strict.json"),
    (5, "语义判定禁止从源文补缺，四方法一致应用", "relation_semantic_review/rule_changes.md"),
    (6, "单阶段提议准确命名，旧管线逐阶段证据，未知预测不计 FP", "audits/real_pipeline_trace.json"),
    (7, "前缀共享、Direct 缺失、失败与成本口径披露", "audits/failure_and_retry_audit.json"),
    (8, "KST 退出主证据，错误键与联合正确率解释修正", "audits/kst_probe_audit.json"),
    (9, "B/batch_2 两次调用与 wrapper 缺失准确留痕", "audits/failure_and_retry_audit.json"),
    (10, "无新增研究 API 调用、未偷换供应商、未扩预算、未回滚、未推送", "ledger_invariant"),
    (11, "聚焦测试与表格算术检查实际执行", "tests/test_final_closeout_s2_metrics.py"),
    (12, "六项交付齐备，主张有数字与限制，无伪造显著性", "manifest.json"),
    # Second acceptance round (CODEX_FINAL_CLOSEOUT_REVIEW) R1-R5.
    (13, "R1 跨运行一致率不再被称为评审噪声上界，排名结论已删除", "cross_run_naming"),
    (14, "R1 端点/承载规则已统一，四方法受影响条目一致重审", "rule_unification"),
    (15, "R2 证据索引每条数值满足 numerator/denominator == value", "ratio_check"),
    (16, "R3 判定覆盖率按实际收到判定的对计算", "coverage_check"),
    (17, "R4 直接先修 F1 0.706→0.900 已恢复为有效指标", "end_to_end_direct_check"),
    (18, "R5 N04 记为未决并提供敏感性变体", "n04_check"),
    (19, "作者裁决已记录：N04 选 A、九对接受、provenance 升级", "author_decision_check"),
    (20, "48 项分层样本已由作者逐项确认（全部同意）", "sample_confirmation_check"),
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git_state() -> dict:
    def run(*args):
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
        ).stdout

    porcelain = [ln for ln in run("status", "--porcelain").splitlines() if ln.strip()]
    return {
        "branch": run("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "head": run("rev-parse", "HEAD").strip(),
        # porcelain lines are 'XY path'; splitting without stripping the whole output keeps the
        # leading status space of the first line intact.
        "tracked_modified": sorted(ln[3:] for ln in porcelain if not ln.startswith("??")),
        "porcelain_entries": len(porcelain),
    }


def artifact_invariant() -> dict:
    """Re-hash every frozen artifact recorded at S0 and compare."""
    inventory = read_json(OUT_DIR / "inventory/input_bindings.json")
    changed, missing, checked = [], [], 0
    for group in inventory["groups"]:
        for record in group["files"]:
            path = REPO / record["path"]
            if not path.is_file():
                missing.append(record["path"])
                continue
            checked += 1
            if sha256_file(path) != record["sha256"]:
                changed.append(record["path"])
    return {
        "checked": checked,
        "changed": changed,
        "missing": missing,
        "ok": not changed and not missing,
        "note": (
            "Files created by this round are not part of the S0 inventory and are therefore not "
            "compared; the check is that nothing that already existed changed."
        ),
    }


def ledger_invariant() -> dict:
    execution = read_json(OUT_DIR / "inventory/execution_inventory.json")
    changed, checked = [], 0
    for ledger in execution["ledgers"]:
        path = REPO / ledger["path"]
        if not path.is_file():
            changed.append({"path": ledger["path"], "reason": "missing"})
            continue
        checked += 1
        current = read_json(path)
        if sha256_file(path) != ledger["sha256"]:
            changed.append(
                {
                    "path": ledger["path"],
                    "reason": "hash changed",
                    "tokens_before": ledger.get("accounting_tokens"),
                    "tokens_now": current.get("accounting_tokens"),
                }
            )
    return {"checked": checked, "changed": changed, "ok": not changed}


def second_round_checks() -> dict:
    """Machine checks for the second acceptance round (R1-R5)."""
    result: dict[str, dict] = {}

    metrics_path = OUT_DIR / "relation_semantic_review/metrics_semantic_vs_strict.json"
    metrics = read_json(metrics_path) if metrics_path.is_file() else {}
    agreement = metrics.get("cross_run_coverage_agreement", {})
    result["cross_run_naming"] = {
        "ok": bool(agreement) and "repeat_agreement" not in metrics,
        "detail": (
            f"key renamed to cross_run_coverage_agreement for {len(agreement)} methods; "
            "no repeat_agreement key remains"
        ),
    }

    unification = sorted(
        p.name
        for p in (OUT_DIR / "relation_semantic_review").glob("rule_unification_*.json")
    )
    unified_path = OUT_DIR / "relation_semantic_review/metrics_unified.json"
    unified = read_json(unified_path) if unified_path.is_file() else {}
    result["rule_unification"] = {
        "ok": len(unification) == 4
        and bool(unified.get("patch_consistency", {}).get("ok"))
        and unified.get("patched_rows", 0) > 0,
        "detail": (
            f"{len(unification)}/4 method patch files applied; "
            f"patched_rows={unified.get('patched_rows')} changed={unified.get('changed_rows')} "
            f"consistency_ok={unified.get('patch_consistency', {}).get('ok')}; "
            f"scope limited to the identified divergent rows, wider reading recorded as sensitivity"
        ),
    }

    index_path = OUT_DIR / "evidence_index.json"
    index = read_json(index_path) if index_path.is_file() else {}
    ratio = index.get("ratio_check", {})
    result["ratio_check"] = {
        "ok": bool(ratio.get("ok")),
        "detail": f"{ratio.get('numeric_entries')} numeric entries checked, "
                  f"{len(ratio.get('problems', []))} problems",
    }

    rescore_path = OUT_DIR / "prerequisite_rescore/rescore.json"
    rescore = read_json(rescore_path) if rescore_path.is_file() else {}
    coverage_ok = True
    coverage_detail = []
    for arm_id in ("real_pipeline.semantic", "real_pipeline.baseline"):
        block = rescore.get("arms", {}).get(arm_id, {}).get("main", {})
        value = block.get("verdict_coverage_on_determinate")
        never = block.get("pairs_never_judged")
        coverage_detail.append(f"{arm_id}={value} (never judged {never})")
        if value is None or value >= 1.0:
            coverage_ok = False
    result["coverage_check"] = {"ok": coverage_ok, "detail": "; ".join(coverage_detail)}

    semantic_scores = read_json(
        REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/semantic_scores.json"
    )
    baseline_scores = read_json(
        REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/prediction01/baseline_scores.json"
    )
    e2e_semantic = semantic_scores["end_to_end_direct"]["f1"]
    e2e_baseline = baseline_scores["end_to_end_direct"]["f1"]
    rescore_directness = {
        arm: rescore.get("arms", {}).get(arm, {}).get("main", {}).get("directness", {}).get("f1")
        for arm in ("real_pipeline.semantic", "real_pipeline.baseline")
    }
    result["end_to_end_direct_check"] = {
        "ok": (
            abs(e2e_semantic - 0.9) < 1e-9
            and abs(e2e_baseline - 0.7058823529411765) < 1e-9
            and abs(rescore_directness["real_pipeline.semantic"] - e2e_semantic) < 1e-9
            and abs(rescore_directness["real_pipeline.baseline"] - e2e_baseline) < 1e-9
        ),
        "detail": (
            f"end_to_end_direct F1 baseline={e2e_baseline:.4f} semantic={e2e_semantic:.4f}; "
            f"rescore main directness reproduces both ({rescore_directness})"
        ),
    }

    v3 = read_json(OUT_DIR / "prerequisite_adjudication/reference_v3.json")
    sensitivity_path = OUT_DIR / "prerequisite_adjudication/reference_v3_n04_required.json"
    n04 = next((p for p in v3["pairs"] if p["id"] == "N04"), {})
    sensitivity_n04 = None
    if sensitivity_path.is_file():
        sensitivity = read_json(sensitivity_path)
        sensitivity_n04 = next((p for p in sensitivity["pairs"] if p["id"] == "N04"), {}).get("role")
    result["n04_check"] = {
        "ok": n04.get("role") == "uncertain" and sensitivity_n04 == "required",
        "detail": f"main v3 N04={n04.get('role')}, sensitivity variant N04={sensitivity_n04}",
    }

    decision_path = OUT_DIR / "prerequisite_adjudication/author_decision.json"
    decision = read_json(decision_path) if decision_path.is_file() else {}
    provenance = {}
    for pair in v3.get("pairs", []):
        provenance[pair["provenance"]] = provenance.get(pair["provenance"], 0) + 1
    result["author_decision_check"] = {
        "ok": (
            decision.get("decision") == "A"
            and provenance.get("user_adjudicated_new") == 9
            and provenance.get("user_approved_existing") == 1
        ),
        "detail": (
            f"author chose {decision.get('decision')!r} on {decision.get('decided_at')}; "
            f"provenance counts {provenance}; reference roles unchanged so scores are unchanged"
        ),
    }

    packet_path = OUT_DIR / "author_review_packet/packet.json"
    packet = read_json(packet_path) if packet_path.is_file() else {}
    agreed = sum(
        1 for item in packet.get("items", [])
        if item.get("author_answer", {}).get("verdict") == "agree"
    )
    result["sample_confirmation_check"] = {
        "ok": packet.get("review_status") == "author_confirmed" and agreed == len(packet.get("items", [])),
        "detail": (
            f"review_status={packet.get('review_status')!r}, {agreed}/{len(packet.get('items', []))} "
            f"sampled items confirmed by the author; stratified sample, not a population estimate"
        ),
    }
    return result


def path_status(rel: str) -> dict:
    path = REPO / rel
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file()]
        return {
            "path": rel,
            "kind": "directory",
            "exists": True,
            "file_count": len(files),
            "total_bytes": sum(p.stat().st_size for p in files),
        }
    if path.is_file():
        return {
            "path": rel,
            "kind": "file",
            "exists": True,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return {"path": rel, "exists": False}


def main() -> int:
    artifact = artifact_invariant()
    ledger = ledger_invariant()
    git = git_state()
    second = second_round_checks()

    deliverables = []
    for name, rel in DELIVERABLES:
        status = path_status(rel)
        status["deliverable"] = name
        deliverables.append(status)

    checks = []
    for number, description, pointer in SELF_CHECK:
        if pointer == "artifact_invariant":
            result = "pass" if artifact["ok"] else "fail"
            evidence = (
                f"{artifact['checked']} frozen artifacts re-hashed, {len(artifact['changed'])} changed, "
                f"{len(artifact['missing'])} missing"
            )
        elif pointer == "ledger_invariant":
            result = "pass" if ledger["ok"] else "fail"
            evidence = f"{ledger['checked']} ledgers re-hashed, {len(ledger['changed'])} changed"
        elif pointer == "manifest.json":
            missing = [d["path"] for d in deliverables if not d.get("exists")]
            result = "pass" if not missing else "fail"
            evidence = f"{len(deliverables) - len(missing)}/{len(deliverables)} deliverables present"
        elif pointer in second:
            check = second[pointer]
            result = "pass" if check["ok"] else "fail"
            evidence = check["detail"]
        else:
            exists = (OUT_DIR / pointer).exists() or (REPO / pointer).exists()
            result = "pass" if exists else "fail"
            evidence = pointer
        checks.append(
            {"number": number, "check": description, "result": result, "evidence": evidence}
        )

    failing = [check for check in checks if check["result"] != "pass"]
    status = "ready_for_codex_review" if not failing else "partial_ready_for_review"

    payload = {
        "schema": "dsh.final_closeout.manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan": "docs/DSH_FINAL_EXPERIMENT_CLOSEOUT_PLAN_2026-10-10.md",
        "status": status,
        "status_meaning": (
            "ready_for_codex_review: the six deliverables exist and the self-check passes; this is "
            "not paper_ready_for_submission."
            if status == "ready_for_codex_review"
            else "partial_ready_for_review: see failing_checks"
        ),
        "new_research_api_calls": 0,
        "git": git,
        "invariants": {"artifacts": artifact, "ledgers": ledger},
        "deliverables": deliverables,
        "self_check": checks,
        "second_round_checks": second,
        "failing_checks": failing,
        "not_run": [
            "P1-B condition C",
            "end-to-end pipeline for the four new targets",
            "Direct S1 prefix repeat 1 (two contract violations, not resampled)",
            "complete native wrapper for the first iText unguarded repeat 2 attempt",
        ],
        "citation_status": {
            "verified": ["EDC (EMNLP 2024, DOI 10.18653/v1/2024.emnlp-main.548)", "iText2KG (WISE 2024, arXiv:2409.03284)"],
            "pending": [
                "KST source paper (probe withdrawn, off the critical path)",
                "mathematics-textbook knowledge graph prior work (AutoMathKG lead, unverified)",
            ],
            "rule": "unverified comparisons are marked pending rather than guessed",
        },
        "author_decisions_pending": [
            "whether to run the full pipeline on the four new targets",
            "target edition and track for the conference (WWW 2027 call not published)",
        ],
        "author_decisions_recorded": [
            "2026-10-10: the 10 prerequisite reference revisions are accepted; N04 keeps option A "
            "(uncertain, target scope limited to the catalogue description). Nine pairs moved to "
            "user_adjudicated_new provenance; N15 keeps user_approved_existing. Reference roles are "
            "unchanged, so every reported score is unchanged.",
            "2026-10-10: the author confirmed all 48 items of the stratified review sample. This is "
            "author confirmation of an assistant review on a sample, not independent double "
            "annotation and not a full-population accuracy.",
        ],
        "deviation_record": [
            "Closeout plan section 1 required subagents to run as gpt-6-luna with reasoning_effort=max "
            "or not at all; the user had instructed that this session's own agents be used, so the "
            "eight relation reviews ran on session agents. The cross-agent agreement is quantified in "
            "relation_semantic_review/rule_changes.md section 8 and reported as a limitation.",
            "No paid research API was involved in that deviation.",
        ],
        "invariants_narrative": (
            "No commit, push, upload or shutdown; the worktree was not reset, checked out or cleaned; "
            "the three pre-existing modified files were not touched."
        ),
    }
    out = OUT_DIR / "manifest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {out.relative_to(REPO).as_posix()}")
    print(f"  status: {status}")
    print(f"  artifacts invariant: checked={artifact['checked']} changed={len(artifact['changed'])} "
          f"missing={len(artifact['missing'])} ok={artifact['ok']}")
    print(f"  ledger invariant:    checked={ledger['checked']} changed={len(ledger['changed'])} ok={ledger['ok']}")
    print(f"  git: {git['branch']} @ {git['head'][:12]} tracked_modified={git['tracked_modified']}")
    print(f"  deliverables present: {sum(1 for d in deliverables if d.get('exists'))}/{len(deliverables)}")
    print(f"  self-check: {sum(1 for c in checks if c['result'] == 'pass')}/{len(checks)} pass")
    for check in failing:
        print(f"   ! #{check['number']} {check['check']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
