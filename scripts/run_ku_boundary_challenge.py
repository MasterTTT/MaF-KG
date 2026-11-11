"""Frozen heading-only L2 challenge on historical L1, not a held-out test."""
import argparse
import itertools
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_ku_prerequisite_phase import Native, MODEL, audit, load, read, sha, write

ROOT = Path(__file__).resolve().parents[1]
JOBS = [
    ("ch1_s1", "第一章", "第一节 映射与函数", "第一章第一节_semantic_v1_7_0_opencode_flash_low_v3.json", "第一章第一节_映射与函数.md"),
    ("ch3_s3", "第三章", "第三节 泰勒公式", "第三章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json", "第三章_全章_无习题.md"),
    ("ch7_s5", "第七章", "第五节 可降阶的高阶微分方程", "第七章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json", "第七章_全章_无习题.md"),
]
# Source-reviewed constraints are fixed here before outputs, never model inputs.
GROUPS = {
    "ch1_s1": [
        ("反函数", ["反函数", "函数与其反函数的图形关于直线 y=x 对称", "单调函数必有反函数且反函数单调"], [286, 312]),
        ("复合函数", ["复合函数", "构成复合函数的条件"], [314, 441]),
        ("双曲函数", ["双曲正弦", "双曲余弦", "双曲正切"], [517, 591]),
        ("反双曲函数", ["反双曲正弦", "反双曲余弦", "反双曲正切"], [593, 688]),
    ],
    "ch3_s3": [
        ("佩亚诺形式", ["佩亚诺余项", "泰勒中值定理（带佩亚诺型余项）", "带有佩亚诺余项的麦克劳林公式"], [539, 609]),
        ("拉格朗日形式", ["拉格朗日余项", "泰勒中值定理2", "带有拉格朗日余项的麦克劳林公式", "拉格朗日余项的误差估计式"], [611, 705]),
    ],
    "ch7_s5": [
        ("连续积分型", ["y^(n)=f(x) 型微分方程", "y^(n)=f(x) 型微分方程的通解公式"], [1320, 1344]),
        ("不显含y型", ["y''=f(x,y') 型微分方程", "y''=f(x,y') 型微分方程的求解方法", "降阶法求解 y''=f(x,y') 型微分方程"], [1426, 1458]),
        ("不显含x型", ["y''=f(y,y') 型微分方程", "y''=f(y,y') 型微分方程的求解方法"], [1621, 1642]),
    ],
}


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    snap = out / "snapshot"
    bindings = []

    def copy(src, relative):
        dst = snap / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        bindings.append({"original": str(src), "path": str(dst), "sha256": sha(dst)})
        return dst

    for name in ("build_knowledge_units.py", "run_ku_prerequisite_phase.py", "prospective_native_client.py"):
        copy(ROOT / "scripts" / name, Path("scripts") / name)
    copy(Path(__file__), Path("scripts") / Path(__file__).name)
    copy(ROOT / "docs/ku_boundary_challenge_preflight_2026-10-08.md", Path("protocol.md"))
    for name in ("knowledge_unit_organizer.txt", "knowledge_unit_finalizer.txt"):
        copy(ROOT / "prompts" / name, Path("prompts") / name)
    module = load(snap / "scripts/build_knowledge_units.py", "challenge_prepare")
    jobs, refs = [], {}
    for jid, chapter, section, gn, sn in JOBS:
        gp = copy(Path("E:/MaF-KG/KG") / gn, Path("graphs") / gn)
        sp = copy(Path("E:/MaF-KG/data/raw") / sn, Path("input") / sn)
        data = read(gp)
        if Path(data["diagnostics"]["input_path"]).name != sn:
            raise ValueError("L1 source mismatch")
        outline = module.build_outline([str(sp)])
        nodes = [n for n in data["nodes"] if module.section_of(n, outline, chapter) == section]
        if not nodes:
            raise ValueError("No section nodes")
        cards = module.build_planner_cards(section, nodes, outline, {sn: data})
        goals = module.heading_objectives(section, cards)
        candidates = module._node_candidate_goals(section, nodes, cards, goals)
        pp = out / "plans" / (jid + ".json")
        write(pp, {"nodes": nodes, "cards": cards, "objectives": goals, "member_candidates": candidates})
        bindings.append({"path": str(pp), "sha256": sha(pp)})
        groups, missing = [], []
        lines = sp.read_text(encoding="utf-8").splitlines()
        for title, names, span in GROUPS[jid]:
            ids = []
            for name in names:
                matches = [n for n in nodes if n["name"] == name]
                if len(matches) > 1:
                    raise ValueError("Ambiguous reference identity")
                if not matches:
                    missing.append({"group": title, "name": name})
                else:
                    ids.append(matches[0]["id"])
            groups.append({"name": title, "expected_names": names, "member_ids": ids,
                           "lines": span, "source_excerpt": "\n".join(lines[span[0]-1:span[1]])})
        refs[jid] = {"groups": groups, "l1_missing": missing,
                     "separate_groups": jid != "ch3_s3",
                     "acceptable_layout": "one combined or two complete families" if jid == "ch3_s3" else "distinct goals",
                     "provenance": "assistant_source_constraints_using_prior_user_boundary_decisions; not independent expert Gold"}
        jobs.append({"id": jid, "chapter": chapter, "section": section, "graph": str(gp), "source": str(sp),
                     "plan": str(pp), "node_count": len(nodes), "heading_count": len(goals), "status": "pending"})
    write(out / "reference.json", {"status": "frozen_before_new_output", "development_challenge": True,
                                   "jobs": refs, "not_exhaustive": True})
    bindings.append({"path": str(out / "reference.json"), "sha256": sha(out / "reference.json")})
    write(out / "manifest.json", {"status": "prepared", "snapshot": str(snap), "bindings": bindings, "jobs": jobs,
        "provider": "opencode-go", "model": MODEL, "thinking": "enabled", "reasoning_effort": "low",
        "max_tokens": 12000, "timeout_seconds": 300, "usd_limit": .50, "token_limit": 500000,
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(), "session": str(uuid.uuid4()),
        "attempts": 1, "fallback": False, "json_repair": False, "reference_sha256": sha(out / "reference.json"),
        "scope": "Three previously discussed sections; mixed historical L1; new frozen heading L2 only"})
    print([(j["id"], j["node_count"], j["heading_count"]) for j in jobs], flush=True)
    print("L1 missing:", {k: v["l1_missing"] for k, v in refs.items()}, flush=True)


def execute(out):
    m = read(out / "manifest.json")
    audit(m)
    if m["status"] != "prepared":
        raise ValueError("No implicit rerun or retry")
    module = load(Path(m["snapshot"]) / "scripts/build_knowledge_units.py", "challenge_run")
    module._llm_json = Native(out, m).call
    m["status"] = "running"
    write(out / "manifest.json", m)
    for j in m["jobs"]:
        result = out / "units" / (j["id"] + ".json")
        result.parent.mkdir(exist_ok=True)
        snap = Path(m["snapshot"])
        sys.argv = ["build_knowledge_units.py", j["graph"], "--out", str(result), "--src-files", j["source"],
                    "--sections", j["section"], "--chapter", j["chapter"], "--model", MODEL,
                    "--router-prompt", str(snap / "prompts/knowledge_unit_organizer.txt"),
                    "--finalizer-prompt", str(snap / "prompts/knowledge_unit_finalizer.txt")]
        try:
            if module.main() != 0:
                raise ValueError("Organizer structural gate failed")
            j.update(status="completed", output=str(result), sha256=sha(result))
        except Exception as exc:
            j.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            print("FAILED", j["id"], type(exc).__name__, flush=True)
        write(out / "manifest.json", m)
    m["status"] = "completed" if all(j["status"] == "completed" for j in m["jobs"]) else "partial_failure"
    write(out / "manifest.json", m)


def score(out):
    m = read(out / "manifest.json")
    audit(m)
    refs = read(out / "reference.json")["jobs"]
    results = {}
    for j in m["jobs"]:
        if j["status"] != "completed":
            results[j["id"]] = {"status": j["status"], "no_missing_verdicts_imputed": True}
            continue
        if sha(j["output"]) != j["sha256"]:
            raise ValueError("Output changed")
        data = read(j["output"])
        owner = {}
        for u in data["knowledge_units"]:
            for n in u["centers_on"] + u["contains"]:
                if n["id"] in owner:
                    raise ValueError("Duplicate main membership")
                owner[n["id"]] = u["id"]
        same, separate = [], []
        for g in refs[j["id"]]["groups"]:
            for a, b in itertools.combinations(g["member_ids"], 2):
                same.append({"a": a, "b": b, "pass": a in owner and b in owner and owner[a] == owner[b]})
        if refs[j["id"]]["separate_groups"]:
            for ga, gb in itertools.combinations(refs[j["id"]]["groups"], 2):
                # Anchor pair per distinct teaching family, not all Cartesian pairs.
                if not ga["member_ids"] or not gb["member_ids"]:
                    continue
                a, b = ga["member_ids"][0], gb["member_ids"][0]
                separate.append({"a": a, "b": b, "pass": a in owner and b in owner and owner[a] != owner[b]})
        results[j["id"]] = {"status": "scored", "ku_count": len(data["knowledge_units"]),
            "must_link": {"hits": sum(x["pass"] for x in same), "denominator": len(same), "pairs": same},
            "cannot_link": {"hits": sum(x["pass"] for x in separate), "denominator": len(separate), "pairs": separate},
            "member_coverage": {"hits": len(owner), "denominator": j["node_count"]},
            "l1_missing": refs[j["id"]]["l1_missing"],
            "units": [{"id": u["id"], "name": u["name"], "description": u["description"],
                       "centers": [n["name"] for n in u["centers_on"]],
                       "members": [n["name"] for n in u["centers_on"] + u["contains"]]} for u in data["knowledge_units"]]}
    write(out / "constraint_scores.json", {"reference_sha256": sha(out / "reference.json"), "jobs": results,
        "scope": "Finite core identity constraints, not full KU semantic correctness; descriptions require separate source review"})
    for jid, r in results.items():
        print(jid, r["status"], {k: v for k, v in r.items() if k not in {"units", "must_link", "cannot_link"}}, flush=True)
        for key in ("must_link", "cannot_link"):
            if key in r:
                print(key, r[key]["hits"], "/", r[key]["denominator"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("prepare", "execute", "score"))
    p.add_argument("out", type=Path)
    a = p.parse_args()
    globals()[a.stage](a.out.resolve())
