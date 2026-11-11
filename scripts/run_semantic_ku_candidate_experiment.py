"""One evidence-constrained KU candidate proposal; no labels sent to models."""
import argparse
import copy
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_ku_candidate_retrieval_experiment import endpoint
from run_ku_prerequisite_phase import Native, MODEL, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation

PROPOSER = """你为数学教材知识单元提出待审核的教学先修候选，不直接决定入图。
目标是理解和应用source当前教学目标，不要求掌握完整证明。
根据source实际描述中明确调用的数学概念或操作，匹配eligible_targets中以该能力为中心的既有KU。
每个source最多3个候选，信息不足可输出0个，不能为了填满数量生成依赖。
仅目标内部的局部条件不拆成新知识点。不能因主题相近、公式外观相似、兄弟方法、教材顺序或某通用Concept包含于一个专门KU，就推断整KU是先修。
专门解法不能冒充覆盖任意方程的通用求解能力，证明所需工具不能自动当理解/应用的必需。
只引用提供的实际KU ID。每条用source.members中一个节点的description精确原文片段作为witness_quote，至少4字符；不能改写引文或新增数学事实。
只输出JSON：{"proposals":[{"source_ku":"...","target_ku":"...","witness_node_id":"...","witness_quote":"精确片段","reason":"为什么该中心能力值得作为候选审核"}]}。
"""


def position(unit, chapters):
    return chapters.index(unit["chapter"]), min(c["start_line"] for c in unit["cards"])


def validate_proposals(answer, tasks, units):
    rows = answer.get("proposals")
    if not isinstance(rows, list):
        raise ValueError("Missing proposals")
    task_index = {t["source"]["id"]: t for t in tasks}
    accepted, seen, counts = [], set(), {}
    for r in rows:
        s, t = r.get("source_ku"), r.get("target_ku")
        if s not in task_index or t not in {u["id"] for u in task_index[s]["eligible_targets"]}:
            raise ValueError("Unknown or out-of-scope proposal")
        if (s, t) in seen:
            raise ValueError("Duplicate proposal")
        seen.add((s, t))
        counts[s] = counts.get(s, 0) + 1
        if counts[s] > 3:
            raise ValueError("Per-goal candidate cap exceeded")
        members = {n["id"]: n for n in task_index[s]["source"]["members"]}
        n = members.get(r.get("witness_node_id"))
        quote = r.get("witness_quote")
        if n is None or not isinstance(quote, str) or len(quote) < 4 or quote not in n["description"]:
            raise ValueError("Proposal witness does not match actual source member")
        if not isinstance(r.get("reason"), str) or not r["reason"].strip():
            raise ValueError("Proposal missing reason")
        accepted.append({"source_ku": s, "target_ku": t, "source_name": units[s]["name"],
            "target_name": units[t]["name"], "signals": [], "chain_via": [], "chain_total": 0,
            "n_edges": 0, "proposal_witness": {"node_id": n["id"], "quote": quote},
            "proposal_reason": r["reason"], "note": "Unconfirmed semantic retrieval candidate, not L1 fact"})
    return accepted


def execute(base, reference_path, out):
    out.mkdir(parents=True, exist_ok=False)
    snap = out / "snapshot"
    snap.mkdir()
    for source, name in ((base / "endpoint_catalog.json", "catalog.json"),
                         (base / "candidates.json", "baseline_candidates.json"),
                         (base / "scoped_review01/predictions.json", "baseline_predictions.json"),
                         (base / "scoped_review01/system.txt", "review_system.txt"),
                         (reference_path, "reference.json"), (Path(__file__), Path(__file__).name),
                         (Path(__file__).with_name("run_ku_prerequisite_phase.py"), "native_runner.py"),
                         (Path(__file__).parents[1] / "docs/prerequisite_semantic_candidate_protocol_2026-10-07.md", "protocol.md")):
        shutil.copy2(source, snap / name)
    catalog = read(snap / "catalog.json")
    units = {u["id"]: u for u in catalog["knowledge_units"]}
    chapters = list(dict.fromkeys(u["chapter"] for u in units.values()))
    sources = [u for u in units.values() if u["job_id"] in {"ch3_s3", "ch4_s3", "ch7_s5"}]
    tasks = []
    for u in sources:
        src = {"id": u["id"], "name": u["name"], "goal": u["description"],
               "members": [{"id": n["id"], "name": n["name"], "description": n.get("description", "")}
                           for n in u["full_member_nodes"] if n["id"] in {c["id"] for c in u["centers_on"]}
                           or n["type"] in {"Claim", "Method"}]}
        targets = []
        for v in units.values():
            if position(v, chapters) < position(u, chapters):
                centers = {n["id"] for n in v["centers_on"]}
                targets.append({"id": v["id"], "name": v["name"], "goal": v["description"],
                    "centers": [{"name": n["name"], "aliases": n.get("aliases", []),
                                 "description": n.get("description", "")[:350]}
                                for n in v["full_member_nodes"] if n["id"] in centers]})
        tasks.append({"source": src, "eligible_targets": targets})
    write(out / "proposal_input.json", tasks)
    (out / "proposal_system.txt").write_text(PROPOSER, encoding="utf-8")
    m = {"status": "prepared", "provider": "opencode-go", "model": MODEL, "reasoning_effort": "low",
         "max_tokens": 12000, "timeout_seconds": 300, "usd_limit": .15, "token_limit": 500000,
         "deadline": (datetime.now(timezone.utc)+timedelta(minutes=90)).isoformat(), "session": str(uuid.uuid4()),
         "attempts": 1, "fallback": False, "json_repair": False, "scope": "One development semantic proposal, not held-out Gold evaluation",
         "bindings": [{"path": str(p), "sha256": sha(p)} for p in [*snap.iterdir(), out/"proposal_input.json", out/"proposal_system.txt"]]}
    write(out / "manifest.json", m)
    native = Native(out, m)
    m["status"] = "running"
    write(out / "manifest.json", m)
    try:
        answer, usage = native.call(PROPOSER, json.dumps(tasks, ensure_ascii=False), MODEL)
        proposed = validate_proposals(answer, tasks, units)
        baseline = read(snap / "baseline_candidates.json")["candidates"]
        existing = {(c["source_ku"], c["target_ku"]) for c in baseline}
        new = [c for c in proposed if (c["source_ku"], c["target_ku"]) not in existing]
        write(out / "proposal_validation.json", {"valid_proposals": proposed, "new_candidates": new,
              "witnesses_exact": True, "reference_not_sent": True})
        pool = baseline + new
        write(out / "candidates.json", {"candidates": pool})
        reference = read(snap / "reference.json")["annotations"]
        pred = copy.deepcopy(read(snap / "baseline_predictions.json")["verdicts"])
        partial = score_evaluation({"endpoints": list(units.values()), "candidates": pool,
                                   "predictions": {"verdicts": pred}, "reference": reference})
        write(out / "retrieval_diagnostics.json", {"candidate_direct_recall": partial["candidate_direct_recall"],
              "baseline_candidates": len(baseline), "new_candidates": len(new), "union_candidates": len(pool)})
        tasks_review = [{"index": len(baseline)+i, "edge": c["source_name"]+" depends_on "+c["target_name"],
            "source": endpoint(units[c["source_ku"]]), "target": endpoint(units[c["target_ku"]]),
            "signals": [], "chain_via": []} for i, c in enumerate(new)]
        # Deliberately omit proposer reason/role/witness to avoid anchoring the reviewer.
        for start in range(0, len(tasks_review), 8):
            write(out / f"review_batch_{start//8+1}.json", tasks_review[start:start+8])
        m["review_bindings"] = [{"path": str(p), "sha256": sha(p)} for p in sorted(out.glob("review_batch_*.json"))]
        write(out / "manifest.json", m)
        system = (snap / "review_system.txt").read_text(encoding="utf-8")
        for b in m["review_bindings"]:
            batch = read(b["path"])
            reply, usage = native.call(system, json.dumps(batch, ensure_ascii=False), MODEL)
            rows = reply.get("verdicts")
            if not isinstance(rows, list) or len(rows) != len(batch) or {v.get("index") for v in rows} != {v["index"] for v in batch}:
                raise ValueError("Invalid review indices")
            for v in rows:
                if type(v.get("index")) is not int or not isinstance(v.get("reason"), str):
                    raise ValueError("Invalid review fields")
                role = v.get("role")
                if role not in {"required", "proof_support", "principle_support", "related_or_none", "uncertain"}:
                    raise ValueError("Invalid review role")
                if role == "required" and v.get("directness") not in {"direct", "indirect"}:
                    raise ValueError("Missing directness")
                if role != "required" and v.get("directness") is not None:
                    raise ValueError("Unexpected directness")
                c = new[v["index"]-len(baseline)]
                row = {"index": v["index"], "source_ku": c["source_ku"], "target_ku": c["target_ku"],
                       "label": role, "reason": v["reason"]}
                if role == "required":
                    row["directness"] = v["directness"]
                pred.append(row)
            write(out / "predictions.json", {"verdicts": pred})
        write(out / "predictions.json", {"verdicts": pred})
        for name, refs in (("mixed", reference), ("human_only", [r for r in reference if r["provenance"]=="human_user_adjudicated"])):
            data = {"endpoints": list(units.values()), "candidates": pool, "predictions": {"verdicts": pred}, "reference": refs}
            write(out / f"{name}_input.json", data)
            write(out / f"{name}_scores.json", score_evaluation(data))
    except Exception as exc:
        m.update(status="failed", error_type=type(exc).__name__)
        write(out / "manifest.json", m)
        raise
    m.update(status="completed", reused_verdicts=len(baseline), new_verdicts=len(new), valid_proposals=len(proposed))
    write(out / "manifest.json", m)
    print(json.dumps(read(out / "retrieval_diagnostics.json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base", type=Path)
    p.add_argument("reference", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    execute(a.base.resolve(), a.reference.resolve(), a.out.resolve())
