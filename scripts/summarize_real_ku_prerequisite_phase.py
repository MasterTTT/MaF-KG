"""Assemble reproducible draft-reference scores and a complete verdict table."""
import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from run_ku_prerequisite_phase import audit, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def summarize(root):
    m = read(root / "manifest.json")
    audit(m)
    supplementary = read(root / "review_completion01/manifest.json")
    if supplementary["status"] != "completed":
        raise ValueError("Supplementary review incomplete")
    ref = read(root / "reference_draft.json")
    if sha(root / "reference_draft.json") != supplementary["reference_sha256"]:
        raise ValueError("Reference changed after prediction")
    candidates = read(root / "candidates.json")["candidates"]
    predictions = read(root / "review_completion01/predictions.json")
    if len(predictions["verdicts"]) != len(candidates):
        raise ValueError("Incomplete candidate coverage")
    ku = read(root / "ku.json")["knowledge_units"]
    inputs = {"endpoints": ku, "candidates": candidates, "predictions": predictions,
              "reference": ref["annotations"]}
    scores = score_evaluation(inputs)
    write(root / "score_input.json", inputs)
    write(root / "scores.json", scores)
    named = {u["id"]: u["name"] for u in ku}
    labels = {(a["target_ku"], a["prerequisite_ku"]): a for a in ref["annotations"]}
    candidate_pairs = {(c["source_ku"], c["target_ku"]) for c in candidates}
    rows = []
    for v in predictions["verdicts"]:
        key = (v["source_ku"], v["target_ku"])
        r = labels.get(key)
        rows.append({**v, "dependent_name": named[key[0]], "prerequisite_name": named[key[1]],
                     "reference_id": r["id"] if r else None,
                     "reference_role": r["role"] if r else "unlabeled"})
    missing = [a for a in ref["annotations"] if a["role"] == "required"
               and (a["target_ku"], a["prerequisite_ku"]) not in candidate_pairs]
    write(root / "verdict_table.json", {"rows": rows, "missing_draft_direct_edges": missing})
    budget = read(root / "budget.json")
    primary_failure = read(root.parent / "run01/budget.json")
    summary = {"status": "execution completed with declared supplementary metadata gate; human Gold pending",
        "primary_review_status": m["stages"]["review"], "supplementary_review_status": supplementary["status"],
        "sections": len(m["jobs"]), "knowledge_units": len(ku),
        "nodes": len(read(root / "graph.json")["nodes"]),
        "unique_typed_relations": len(read(root / "graph.json")["relations"]),
        "candidates": len(candidates), "accepted": sum(v["accept"] for v in predictions["verdicts"]),
        "rejected": sum(not v["accept"] for v in predictions["verdicts"]),
        "reference_roles": dict(Counter(a["role"] for a in ref["annotations"])),
        "scored_candidate_pairs": scores["scope"]["candidate_pairs_with_determinate_labels"],
        "unlabeled_candidate_pairs": sum((c["source_ku"], c["target_ku"]) not in labels for c in candidates),
        "run02_requests": len(budget["requests"]), "run02_accounting_tokens": budget["accounting_tokens"],
        "run02_accounting_usd": budget["accounting_usd"],
        "run01_unknown_usage_reservation_usd": primary_failure["accounting_usd"],
        "total_conservative_accounting_usd": budget["accounting_usd"] + primary_failure["accounting_usd"],
        "cost_caveat": "Local conservative estimate using peak rates, not OpenCode subscription billing.",
        "metrics": scores, "no_expert_gold_claim": True}
    scoped = root / "scoped_review01"
    if (scoped / "manifest.json").exists():
        scoped_manifest = read(scoped / "manifest.json")
        summary["scoped_diagnostic_status"] = scoped_manifest["status"]
        if scoped_manifest["status"] == "completed":
            summary["scoped_diagnostic_metrics"] = read(scoped / "scores.json")
            summary["scoped_diagnostic_role_counts"] = dict(Counter(
                r["label"] for r in read(scoped / "predictions.json")["verdicts"]))
    write(root / "summary.json", summary)
    lines = ["# 真实KU先修审核全部结果", "", "方向为：目标KU depends_on 先修KU。未标注不代表负例；有限草案不是专家Gold。", "",
             "|目标KU|先修KU|审核|草案角色|审核理由|", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"|{r['dependent_name']}|{r['prerequisite_name']}|{'接受' if r['accept'] else '拒绝'}|{r['reference_role']}|{r['reason'].replace('|','/')}|")
    lines.extend(["", "## 未进入候选池的直接先修草案"])
    for a in missing:
        lines.append(f"- {a['id']} {a['prerequisite_name']} → {a['target_name']}：{a['reason']}")
    (root / "verdict_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = [Path(__file__), Path(__file__).with_name("score_ku_prerequisite_evaluation.py"),
             Path(__file__).with_name("freeze_real_ku_prerequisite_reference.py"),
             Path(__file__).with_name("export_ku_prerequisite_endpoints.py"),
             Path(__file__).with_name("export_ku_prerequisite_human_packet.py")]
    for f in files:
        dest = root / "evaluation_software" / f.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(f, dest)
    artifacts = [p for p in root.rglob("*") if p.is_file() and p.suffix in {".json", ".md", ".py", ".txt"}]
    write(root / "artifact_index.json", {"files": [{"path": str(p), "sha256": sha(p)} for p in artifacts
        if p.name != "artifact_index.json"], "scope": "Generated after evaluation; does not replace pre-prediction freeze hashes."})
    print(json.dumps({k: v for k, v in summary.items() if k not in {"metrics", "scoped_diagnostic_metrics"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    summarize(p.parse_args().root.resolve())
