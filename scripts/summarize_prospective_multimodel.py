"""Summarize operational data, keeping semantic quality explicitly unscored."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(output):
    manifest = read(output / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        directory = Path(job["directory"])
        row = {key: job[key] for key in ("model", "section", "name", "status")}
        if job["status"] == "completed":
            graph_path = directory / "graph.json"
            if hashlib.sha256(graph_path.read_bytes()).hexdigest() != job["graph_sha256"]:
                raise ValueError("Completed graph changed")
            graph = read(graph_path)
            native = [read(path) for path in sorted((directory / "native_responses").glob("*.json"))]
            inputs = sum(r.get("usage", {}).get("input_tokens", r.get("usage", {}).get("prompt_tokens", 0)) for r in native)
            outputs = sum(r.get("usage", {}).get("output_tokens", r.get("usage", {}).get("completion_tokens", 0)) for r in native)
            row.update(nodes=len(graph["nodes"]), relations=len(graph["relations"]),
                       node_types=dict(Counter(n["type"] for n in graph["nodes"])),
                       relation_types=dict(Counter(r["type"] for r in graph["relations"])),
                       calls=len(native), input_tokens=inputs, output_tokens=outputs,
                       total_tokens=inputs+outputs, job_wall_seconds=job.get("elapsed_seconds"),
                       graph_sha256=job["graph_sha256"], semantic_quality="not_yet_reviewed")
        rows.append(row)
    budgets = [read(path) for path in sorted((output / "budgets").glob("*.json"))]
    result = {"status":manifest["status"], "jobs":rows,
              "completed_jobs":sum(r["status"] == "completed" for r in rows),
              "estimated_budget_accounting_usd":sum(b["accounting_usd"] for b in budgets),
              "reported_tokens_completed_jobs":sum(r.get("total_tokens",0) for r in rows),
              "limits":["Counts do not measure semantic correctness",
                        "Budgets conservatively price uncached tokens; not provider invoice or subscription balance",
                        "Use job wall seconds, not Extractor api_summary latency: normalized-response parsing hook excludes actual HTTP time",
                        "One repeat; no statistical significance claim; assistants provided source-based reference draft"]}
    (output / "operational_summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines = ["# 五模型运行记录", "", "这里只汇总运行与成本，尚未完成语义质量评分。", "",
             "|模型|小节|管线|状态|节点|关系|调用|Token|",
             "|---|---|---|---|---:|---:|---:|---:|"]
    for r in rows:
        lines.append("|" + "|".join(str(r.get(k,"—")) for k in
            ("model","section","name","status","nodes","relations","calls","total_tokens")) + "|")
    lines += ["", f"完成 {result['completed_jobs']}/{len(rows)} 任务。",
              f"账本估算费用（包括失败保守预留）：{result['estimated_budget_accounting_usd']:.4f} 美元。",
              "", "API归一化钩子内延迟不是网络耗时，不使用该字段比较速度。任务墙钟耗时包含网络、解析和调用间隔。",
              "GLM/MiMo/Kimi使用原生网关默认推理配置，不能与DS/Luna的low宣称等计算预算。"]
    (output / "operational_summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    result = summarize(args.output.resolve())
    print(json.dumps({k:result[k] for k in ("status","completed_jobs","estimated_budget_accounting_usd")},indent=2))
