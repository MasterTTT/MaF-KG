"""Source-backed finite prerequisite judge pilot; draft labels are not human Gold."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from prospective_native_client import install, write
from run_memory_ablation import load_module

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "evaluation/prospective_2026-10-04/prerequisite_draft_v2/candidate_pool.json"
NEUTRAL_UNITS = {
    "T": ("泰勒公式与余项", "理解和应用泰勒多项式近似，计算系数，解释佩亚诺误差阶与拉格朗日误差表达式。"),
    "TP": ("泰勒公式中的佩亚诺余项", "计算泰勒系数并解释o((x-x0)^n)；这是泰勒目标的一个侧面。"),
    "I": ("分部积分法", "理解和应用分部积分公式，选择u和dv并计算剩余积分。"),
    "O1": ("y^(n)=f(x)型方程求解", "反复进行不定积分，得到带n个任意常数的解。"),
    "O2": ("y''=f(x,y')型方程降阶", "令y'=p，化成p'=f(x,p)，获得p的解后恢复y。"),
    "O3": ("y''=f(y,y')型方程降阶", "将p看作y的函数，化成p dp/dy=f(y,p)，获得p的解后恢复y。"),
    "D": ("高阶导数与逐次求导", "理解n阶导数，并用求导法则逐次计算。"),
    "A": ("原函数与不定积分", "理解原函数与不定积分F+C，并执行目标实例中的基本积分。"),
    "PR": ("乘积求导法则", "理解和应用(uv)'=u'v+uv'。"),
    "CR": ("复合函数求导法则", "对复合函数应用链式求导法则。"),
    "S": ("可分离变量方程与分离变量法", "识别可分离变量形式，分离变量并积分。"),
    "Q": ("高阶无穷小比较", "通过beta/alpha的极限为零理解beta=o(alpha)。"),
    "LH": ("洛必达法则", "在规定条件下使用洛必达法则计算未定式极限。"),
    "CM": ("柯西中值定理", "理解并在满足假设的区间上应用柯西中值定理。"),
    "SUB": ("换元积分法", "通过变量代换计算积分。"),
    "LIN": ("一阶线性方程求解", "识别和求解y'+P(x)y=Q(x)型方程。"),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_inputs(pool, explicit_direction=False):
    evidence = {e["id"]: e for e in pool["evidence"]}
    items = [c for c in pool["candidates"] if c["prerequisite_anchor"] is not None]
    records = []
    for index, c in enumerate(items):
        source, target = c["target_anchor"], c["prerequisite_anchor"]
        records.append({"index": index, "source": {"name": NEUTRAL_UNITS[source][0], "goal": NEUTRAL_UNITS[source][1]},
            "target": {"name": NEUTRAL_UNITS[target][0], "goal": NEUTRAL_UNITS[target][1]},
            "source_excerpts": [{k:evidence[e][k] for k in ("source", "line_start", "line_end", "quote")}
                                for e in c["evidence_ids"]]})
        if explicit_direction:
            records[-1]["candidate_edge"] = f"{NEUTRAL_UNITS[source][0]} depends_on {NEUTRAL_UNITS[target][0]}"
            records[-1]["direction_question"] = f"学习{NEUTRAL_UNITS[source][0]}之前，是否必须先掌握{NEUTRAL_UNITS[target][0]}？不能改判反向关系。"
    return items, records


def prepare(output, explicit_direction=False, prompt=None):
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    snapshot.mkdir()
    for source, target in ((POOL, "candidate_pool.json"), (prompt or ROOT / "prompts/ku_depends_review.txt", "prompt.txt"),
                           (ROOT / "agents/Extractor.py", "Extractor.py"),
                           (Path(__file__), "run_finite_prerequisite_pilot.py"),
                           (ROOT / "scripts/prospective_native_client.py", "prospective_native_client.py")):
        shutil.copy2(source, snapshot / target)
    pool = read(snapshot / "candidate_pool.json")
    for binding in pool["bindings"]:
        if digest(binding["path"]) != binding["sha256"]:
            raise ValueError("Candidate source or user decision changed")
    items, inputs = make_inputs(pool, explicit_direction)
    for start in range(0, len(inputs), 11):
        write(snapshot / f"batch_{start//11+1}.json", inputs[start:start+11])
    write(output / "manifest.json", {
        "status": "prepared", "model": "deepseek-v4-flash", "provider": "opencode-go",
        "thinking": "enabled", "reasoning_effort": "low", "max_tokens": 10000,
        "request_timeout": 300, "attempts_per_batch": 1, "local_usd_limit": 0.05,
        "session": str(uuid.uuid4()), "candidate_ids": [c["id"] for c in items],
        "excluded_conditional_ids": [c["id"] for c in pool["candidates"] if c["prerequisite_anchor"] is None],
        "scope": "Exploratory edge-judging pilot on scoped draft anchors, not full KU extraction or prerequisite candidate retrieval. Four labels user-approved; other labels assistant draft.",
        "explicit_direction": explicit_direction,
        "prompt_source": str(prompt or ROOT / "prompts/ku_depends_review.txt"),
        "input_policy": "Neutral capability cards and exact source excerpts; no reference labels, approval flags, reasons, uncertainty fields or prior predicted edges sent.",
        "batches": [{"path": str(p), "status": "pending"} for p in sorted(snapshot.glob("batch_*.json"))],
        "bindings": [{"path": str(p), "sha256": digest(p)} for p in snapshot.iterdir() if p.is_file()]})


def execute(output):
    manifest = read(output / "manifest.json")
    if manifest["status"] != "prepared":
        raise ValueError("No automatic continuation/retry of this exploratory pilot")
    for b in manifest["bindings"]:
        if digest(b["path"]) != b["sha256"]:
            raise ValueError("Frozen prerequisite pilot changed")
    module = load_module(output / "snapshot/Extractor.py")
    install(module, manifest["model"], output, output / "budget.json", manifest["session"], usd_limit=0.05)
    client = module.ProviderChatClient(provider="opencode-go", model=manifest["model"],
        base_url="https://opencode.ai/zen/go/v1", api_key_env="OPENCODE_API_KEY",
        temperature=0.1, timeout=300, max_retries=0, max_tokens=10000,
        use_response_format=False, thinking="enabled", reasoning_effort="low")
    system = (output / "snapshot/prompt.txt").read_text(encoding="utf-8")
    predictions = []
    for index, batch in enumerate(manifest["batches"]):
        records = read(batch["path"])
        user = ("评价口径：理解和应用给出的中心教学目标，不要求先掌握其证明。"
                "判断source学习目标是否需要先掌握target。source_excerpts是教材原文，不是标准答案。"
                "请按系统JSON格式覆盖每个index，不把章节相邻或证明支持自动当作应用必需先修。\n"
                + json.dumps(records, ensure_ascii=False))
        batch["status"], manifest["status"] = "running", "running"
        write(output / "manifest.json", manifest)
        try:
            result = client.complete_json(system, user, label=f"batch_{index+1}")
            verdicts = result["verdicts"]
            by_id = {v["index"]: v for v in verdicts}
            if len(by_id) != len(verdicts) or set(by_id) != {r["index"] for r in records}:
                raise ValueError("Missing, duplicate or out-of-pool index")
            if any(type(v.get("accept")) is not bool or not isinstance(v.get("reason"), str) for v in verdicts):
                raise ValueError("Malformed verdict")
            predictions.extend(verdicts)
            batch["status"] = "completed"
        except Exception as exc:
            batch.update(status="failed", error_type=type(exc).__name__)
            manifest["status"] = "failed"
            write(output / "manifest.json", manifest)
            raise
        write(output / "predictions.json", predictions)
        write(output / "manifest.json", manifest)
        print(f"Completed prerequisite batch {index+1}", flush=True)
    manifest["status"] = "completed"
    write(output / "manifest.json", manifest)
    score(output)


def score(output):
    manifest = read(output / "manifest.json")
    if manifest["status"] != "completed":
        raise ValueError("Only complete pilot predictions may be scored")
    pool = read(output / "snapshot/candidate_pool.json")
    candidates = {c["id"]: c for c in pool["candidates"]}
    predictions = {v["index"]: v for v in read(output / "predictions.json")}
    rows = []
    for index, cid in enumerate(manifest["candidate_ids"]):
        c, p = candidates[cid], predictions[index]
        rows.append({"candidate_id": cid, "draft_label": c["label"], "approval": c["approval"],
            "uncertain": c["uncertain"], "accept": p["accept"], "reason": p["reason"],
            "agrees_with_draft_requiredness": p["accept"] == c["required_prerequisite"]})
    binary = [r for r in rows if r["draft_label"] in ("positive", "negative")]
    support = [r for r in rows if r["draft_label"] == "proof_support"]
    approved = [r for r in rows if r["approval"].startswith("user_approved")]
    write(output / "score.json", {"scope": manifest["scope"],
        "binary_draft_agreement": {"matches": sum(r["agrees_with_draft_requiredness"] for r in binary), "total": len(binary)},
        "nonrequired_support_false_accepts": {"count": sum(r["accept"] for r in support), "total": len(support)},
        "user_approved_requiredness_agreement": {"matches": sum(r["agrees_with_draft_requiredness"] for r in approved), "total": len(approved)},
        "caveat": "Draft agreement is not expert-Gold accuracy; does not measure candidate retrieval, end-to-end KU quality, learning paths, or transitive reduction.",
        "rows": rows})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--explicit-direction", action="store_true")
    parser.add_argument("--prompt", type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.output.resolve(), args.explicit_direction, args.prompt.resolve() if args.prompt else None)
    if args.execute:
        execute(args.output.resolve())
    if args.score:
        score(args.output.resolve())
