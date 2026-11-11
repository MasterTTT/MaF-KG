"""One bounded scope-alignment diagnostic, without changing production review."""
import argparse
import json
import shutil
from pathlib import Path

from run_ku_prerequisite_phase import Native, MODEL, audit, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation

SYSTEM = """你是数学教材知识单元的教学先修关系审核员。
本次目标统一为理解与应用生成KU描述中的数学内容，不要求事先掌握该内容的完整证明。
A depends_on B表示学习A的目标需要先掌握B。请勿判断反向边。
不能因证明使用B、L1 derived_from边、数学特例、教材先后或B的成员被A引用，就自动认可整KU的先修。
重点检查被调用的具体成员是否就是B的核心教学目标；通用概念在某个专门KU出现，不意味着掌握通用概念必须先学该专门KU。
条件化的求解步骤不能推广成所有该类方程均要求某个指定解法；选择性或可替代工具不能当无条件必要。
每对输出角色：required（该目标必需）、principle_support（公式来源等原理支持）、proof_support（证明支持）、related_or_none（非必需或无关）、uncertain（信息不足）。
required必须另标directness=direct或indirect；其他角色directness=null。间接是经其他教学目标承接的依赖，不是数学证明中有若干推导步骤。
证据只能依据提供的实际目标、成员和教材摘录。数学相关不等于教学必要，不能补造教材事实。
只输出JSON：{"verdicts":[{"index":0,"role":"required","directness":"direct","reason":"一句话理由"}]}，覆盖每个index且不得重复。
"""


def execute(root):
    m = read(root / "manifest.json")
    audit(m)
    if read(root / "review_completion01/manifest.json")["status"] != "completed":
        raise ValueError("Finish baseline review before diagnostic")
    out = root / "scoped_review01"
    out.mkdir(exist_ok=False)
    shutil.copy2(__file__, out / Path(__file__).name)
    prompt = out / "system.txt"
    prompt.write_text(SYSTEM, encoding="utf-8")
    catalog = {u["id"]: u for u in read(root / "endpoint_catalog.json")["knowledge_units"]}
    candidates = read(root / "candidates.json")["candidates"]
    tasks = []

    def endpoint(u):
        return {"id": u["id"], "name": u["name"], "goal": u["description"],
            "centers": [n["id"] for n in u["centers_on"]],
            "members": [{"id": n["id"], "name": n["name"], "type": n["type"],
                         "description": n.get("description", "")[:350],
                         "evidence": [{"source": Path(e.get("source_path", "")).name,
                                       "start_line": e.get("start_line"), "end_line": e.get("end_line"),
                                       "quote": e.get("text", "")[:200]} for e in n.get("evidence", [])[:2]]}
                        for n in u["full_member_nodes"]]}
    for i, c in enumerate(candidates):
        tasks.append({"index": i, "edge": c["source_name"] + " depends_on " + c["target_name"],
                      "source": endpoint(catalog[c["source_ku"]]),
                      "target": endpoint(catalog[c["target_ku"]]),
                      "signals": c["signals"], "chain_via": c["chain_via"]})
    for start in range(0, len(tasks), 8):
        write(out / f"batch_{start//8+1}.json", tasks[start:start+8])
    sm = {"status": "prepared", "scope": "Post-baseline scope+payload alignment diagnostic, not single-factor ablation or expert Gold",
          "reference_sha256": sha(root / "reference_draft.json"), "candidate_sha256": sha(root / "candidates.json"),
          "prompt_sha256": sha(prompt), "batches": [{"path": str(p), "sha256": sha(p)} for p in sorted(out.glob("batch_*.json"))],
          "model": MODEL, "provider": "opencode-go", "reasoning_effort": "low", "attempts": 1,
          "fallback": False, "json_repair": False, "member_description_chars": 350,
          "member_evidence_limit": 2, "evidence_text_chars": 200,
          "shared_budget": str(root / "budget.json"), "software_sha256": sha(__file__)}
    write(out / "manifest.json", sm)
    client = Native(root, m)
    predictions = []
    sm["status"] = "running"
    write(out / "manifest.json", sm)
    try:
        for batch in sm["batches"]:
            content = read(batch["path"])
            result, usage = client.call(SYSTEM, json.dumps(content, ensure_ascii=False), MODEL)
            rows = result.get("verdicts")
            if not isinstance(rows, list) or len(rows) != len(content) or {v.get("index") for v in rows} != {v["index"] for v in content}:
                raise ValueError("Invalid index coverage")
            for v in rows:
                if type(v.get("index")) is not int or not isinstance(v.get("reason"), str):
                    raise ValueError("Invalid verdict fields")
                role = v.get("role")
                if role not in {"required", "principle_support", "proof_support", "related_or_none", "uncertain"}:
                    raise ValueError("Invalid role")
                if role == "required" and v.get("directness") not in {"direct", "indirect"}:
                    raise ValueError("Required role missing directness")
                if role != "required" and v.get("directness") is not None:
                    raise ValueError("Nonrequired role has directness")
                c = candidates[v["index"]]
                row = {"index": v["index"], "source_ku": c["source_ku"], "target_ku": c["target_ku"],
                       "label": role, "reason": v["reason"]}
                if role == "required":
                    row["directness"] = v["directness"]
                predictions.append(row)
            write(out / "predictions.json", {"verdicts": predictions})
            print(f"Scoped review {len(predictions)}/{len(tasks)}", flush=True)
    except Exception as exc:
        sm.update(status="failed", error_type=type(exc).__name__)
        write(out / "manifest.json", sm)
        raise
    data = {"endpoints": list(catalog.values()), "candidates": candidates,
            "predictions": {"verdicts": predictions}, "reference": read(root / "reference_draft.json")["annotations"]}
    write(out / "score_input.json", data)
    write(out / "scores.json", score_evaluation(data))
    sm.update(status="completed", verdict_count=len(predictions))
    write(out / "manifest.json", sm)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    execute(p.parse_args().root.resolve())
