"""Archive bounded retrieval comparisons without changing labels or predictions."""
import argparse
import json
import shutil
from pathlib import Path

from run_ku_prerequisite_phase import read, sha, write


def summarize(root, adjudicated):
    rows = {"l1_only": {"candidate_count": 21,
            "mixed": read(adjudicated / "scoped_mixed_scores.json"),
            "human_only": read(adjudicated / "scoped_human_only_scores.json")}}
    for label, folder in (("bm25", "run01"), ("semantic", "semantic01")):
        run = root / folder
        m = read(run / "manifest.json")
        if m["status"] != "completed":
            raise ValueError("Incomplete retrieval experiment")
        for b in m["bindings"]:
            if sha(b["path"]) != b["sha256"]:
                raise ValueError("Frozen experiment input changed")
        scores = read(run / "mixed_scores.json")
        rows[label] = {"candidate_count": scores["scope"]["candidate_edge_count"],
                       "mixed": scores, "human_only": read(run / "human_only_scores.json"),
                       "budget": read(run / "budget.json"), "new_verdicts": m["new_verdicts"]}
        dependencies = run / "runtime_dependencies"
        dependencies.mkdir(exist_ok=True)
        for name in ("run_ku_candidate_retrieval_experiment.py", "prospective_native_client.py",
                     "score_ku_prerequisite_evaluation.py", "requirements_candidate_retrieval.txt"):
            shutil.copy2(Path(__file__).with_name(name), dependencies / name)
        write(run / "runtime_dependency_manifest.json", {
            "status": "Post-run supplemental source archive; pre-call hashes remain in experiment manifest",
            "files": [{"path": str(p), "sha256": sha(p)} for p in dependencies.iterdir()]})
        artifacts = [p for p in run.rglob("*") if p.is_file() and p.suffix in {".json", ".txt", ".md", ".py"}]
        write(run / "artifact_index.json", {"files": [{"path": str(p), "sha256": sha(p)} for p in artifacts
                                                     if p.name != "artifact_index.json"]})
    write(root / "comparison.json", {"scope": "Known development cases, not held-out or complete expert Gold",
        "arms": rows, "known_reference": "4 user-adjudicated pairs + 16 assistant drafts, unchanged across arms",
        "total_new_requests": sum(len(r["budget"]["requests"]) for r in rows.values() if "budget" in r),
        "accounting_tokens": sum(r["budget"]["accounting_tokens"] for r in rows.values() if "budget" in r),
        "peak_rate_estimated_usd": sum(r["budget"]["accounting_usd"] for r in rows.values() if "budget" in r),
        "cost_note": "Not provider subscription billing"})
    lines = ["# 新候选及独立审核结果", "", "保留旧21项预测，本表只展示新增项。未标注项不自动算对或错。", "",
             "|通道|目标|前置候选|审核角色|理由|", "|---|---|---|---|---|"]
    for label, folder in (("BM25", "run01"), ("语义提议", "semantic01")):
        run = root / folder
        catalogue = read(run / "snapshot/catalog.json")["knowledge_units"]
        names = {u["id"]: u["name"] for u in catalogue}
        pred = read(run / "predictions.json")["verdicts"]
        baseline = {(v["source_ku"], v["target_ku"]) for v in read(run / "snapshot/baseline_predictions.json")["verdicts"]}
        for p in pred:
            if (p["source_ku"], p["target_ku"]) not in baseline:
                lines.append(f"|{label}|{names[p['source_ku']]}|{names[p['target_ku']]}|{p['label']}|{p['reason'].replace('|','/')}|")
    (root / "new_verdicts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Saved comparison, all new verdicts, and verified artifact indices")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("adjudicated", type=Path)
    a = p.parse_args()
    summarize(a.root.resolve(), a.adjudicated.resolve())
