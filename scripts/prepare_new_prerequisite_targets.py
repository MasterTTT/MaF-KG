"""Prepare and organize new prerequisite targets without generating dependencies."""
import argparse
import copy
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_ku_prerequisite_phase import Native, MODEL, audit, load, read, sha, write

ROOT = Path(__file__).resolve().parents[1]
JOBS = [
    ("ch1_s3", "第一章", "第三节 函数的极限", "第一章第三节_semantic_v1_7_0_opencode_flash_high.json", "第一章第三节.md", ["g1"], False),
    ("ch5_s1", "第五章", "第一节 定积分的概念与性质", "第五章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json", "第五章_全章_无习题.md", ["g2"], False),
    ("ch5_s2", "第五章", "第二节 微积分基本公式", "第五章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json", "第五章_全章_无习题.md", ["g2", "g3"], False),
    ("ch5_s4", "第五章", "第四节 反常积分", "第五章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json", "第五章_全章_无习题.md", None, True),
    ("ch7_s3", "第七章", "第三节 齐次方程", "第七章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json", "第七章_全章_无习题.md", None, True),
]


def prepare(base, out):
    out.mkdir(parents=True, exist_ok=False)
    snap = out / "snapshot"
    bindings = []

    def copy(src, relative):
        dest = snap / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        bindings.append({"path": str(dest), "sha256": sha(dest), "original": str(src)})
        return dest
    for name in ("build_knowledge_units.py", "build_ku_depends_candidates.py", "run_ku_prerequisite_phase.py", "prospective_native_client.py"):
        copy(ROOT / "scripts" / name, Path("scripts") / name)
    copy(Path(__file__), Path("scripts") / Path(__file__).name)
    for name in ("knowledge_unit_organizer.txt", "knowledge_unit_finalizer.txt"):
        copy(ROOT / "prompts" / name, Path("prompts") / name)
    copy(base / "endpoint_catalog.json", Path("old_catalog.json"))
    copy(base / "graph.json", Path("old_graph.json"))
    copy(base / "scoped_review01/system.txt", Path("review_system.txt"))
    copy(ROOT / "docs/prerequisite_new_targets_protocol_2026-10-07.md", Path("protocol.md"))
    copied = {}
    module = load(snap / "scripts/build_knowledge_units.py", "new_target_prepare")
    jobs = []
    for jid, chapter, section, gname, sname, chosen, query in JOBS:
        if gname not in copied:
            copied[gname] = copy(Path("E:/MaF-KG/KG") / gname, Path("graphs") / gname)
        if sname not in copied:
            copied[sname] = copy(Path("E:/MaF-KG/data/raw") / sname, Path("input") / sname)
        graph, source = copied[gname], copied[sname]
        data = read(graph)
        if Path(data["diagnostics"]["input_path"]).name != source.name:
            raise ValueError("L1 structural source does not match selected textbook")
        outline = module.build_outline([str(source)])
        nodes = [n for n in data["nodes"] if module.section_of(n, outline, chapter) == section]
        if not nodes:
            raise ValueError("Requested new section has no attributed nodes; stop before API calls")
        cards = module.build_planner_cards(section, nodes, outline, {source.name: data})
        objectives = module.heading_objectives(section, cards)
        if chosen and set(chosen)-{o["goal_id"] for o in objectives}:
            raise ValueError("Selected textbook heading missing")
        module._node_candidate_goals(section, nodes, cards, objectives)
        plan = out / "plans" / (jid + ".json")
        write(plan, {"nodes": nodes, "cards": cards, "objectives": objectives})
        bindings.append({"path": str(plan), "sha256": sha(plan)})
        jobs.append({"id": jid, "chapter": chapter, "section": section, "graph": str(graph),
                     "source": str(source), "plan": str(plan), "select_goals": chosen,
                     "is_query": query, "status": "pending", "node_count": len(nodes), "headings": objectives})
    write(out / "manifest.json", {"status": "prepared", "snapshot": str(snap), "bindings": bindings,
        "jobs": jobs, "model": MODEL, "provider": "opencode-go", "reasoning_effort": "low",
        "max_tokens": 12000, "timeout_seconds": 300, "usd_limit": .50, "token_limit": 1000000,
        "deadline": (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(), "session": str(uuid.uuid4()),
        "attempts": 1, "fallback": False, "json_repair": False,
        "stages": {"organize": "pending", "reference": "pending", "predict": "pending"}})
    print([(j["id"], j["node_count"], len(j["headings"])) for j in jobs])


def organize(out):
    m = read(out / "manifest.json")
    audit(m)
    if m["stages"]["organize"] != "pending":
        raise ValueError("No automatic continuation")
    snap = Path(m["snapshot"])
    module = load(snap / "scripts/build_knowledge_units.py", "new_target_organizer")
    module._llm_json = Native(out, m).call
    m["stages"]["organize"] = "running"
    write(out / "manifest.json", m)
    try:
        for j in m["jobs"]:
            result = out / "units" / (j["id"] + ".json")
            result.parent.mkdir(exist_ok=True)
            sys.argv = ["build_knowledge_units.py", j["graph"], "--out", str(result),
                        "--src-files", j["source"], "--sections", j["section"], "--chapter", j["chapter"],
                        "--model", MODEL, "--router-prompt", str(snap / "prompts/knowledge_unit_organizer.txt"),
                        "--finalizer-prompt", str(snap / "prompts/knowledge_unit_finalizer.txt")]
            if module.main() != 0:
                raise ValueError("KU structural gate failed")
            j.update(status="completed", output=str(result), sha256=sha(result))
            write(out / "manifest.json", m)
        assemble(out)
    except Exception as exc:
        m["stages"]["organize"] = "failed"
        m["error_type"] = type(exc).__name__
        write(out / "manifest.json", m)
        raise


def assemble(out):
    m = read(out / "manifest.json")
    audit(m)
    catalogue = copy.deepcopy(read(Path(m["snapshot"]) / "old_catalog.json")["knowledge_units"])
    graph = read(Path(m["snapshot"]) / "old_graph.json")
    nodes = {n["id"]: n for n in graph["nodes"]}
    relations = {(r["source_id"], r["type"], r["target_id"]): r for r in graph["relations"]}
    queries = []
    for j in m["jobs"]:
        if j["status"] != "completed" or sha(j["output"]) != j["sha256"]:
            raise ValueError("Incomplete or changed KU output")
        data = read(j["output"])
        plan = read(j["plan"])
        nid = {n["id"]: n for n in plan["nodes"]}
        goals = data["objective_plans"][j["section"]]
        for u, goal in zip(data["knowledge_units"], goals, strict=True):
            if j["select_goals"] and goal["goal_id"] not in j["select_goals"]:
                continue
            member_ids = [n["id"] for n in u["centers_on"]+u["contains"]]
            catalogue.append({**u, "job_id": j["id"], "goal_id": goal["goal_id"], "chapter": j["chapter"],
                "cards": [c for c in data["planner_cards"][j["section"]] if c["card_id"] in goal["card_ids"]],
                "full_member_nodes": [nid[n] for n in member_ids]})
            if j["is_query"]:
                queries.append(u["id"])
            for n in member_ids:
                if n in nodes and (nodes[n]["name"],nodes[n]["type"])!=(nid[n]["name"],nid[n]["type"]):
                    raise ValueError("Conflicting shared node identity")
                nodes.setdefault(n, nid[n])
    centre_owner = {}
    for u in catalogue:
        for n in u["centers_on"]:
            if n["id"] in centre_owner:
                raise ValueError("Duplicate global center; do not silently reassign")
            centre_owner[n["id"]] = u["id"]
    for p in dict.fromkeys(j["graph"] for j in m["jobs"]):
        for r in read(p)["relations"]:
            if r["source_id"] in nodes and r["target_id"] in nodes:
                relations.setdefault((r["source_id"],r["type"],r["target_id"]),r)
    chapter_numbers = {"第一章":1,"第二章":2,"第三章":3,"第四章":4,"第五章":5,"第六章":6,"第七章":7}
    catalogue.sort(key=lambda u:(chapter_numbers[u["chapter"]],min(c["start_line"] for c in u["cards"]),u["id"]))
    write(out / "catalog.json", {"knowledge_units": catalogue})
    write(out / "ku.json", {"knowledge_units": [{k:v for k,v in u.items() if k not in {"cards","full_member_nodes"}} for u in catalogue]})
    write(out / "graph.json", {"nodes":list(nodes.values()), "relations":list(relations.values())})
    write(out / "query_config.json", {"dependent_ids":queries, "max_proposals_per_query":3,
        "scope":"New prerequisite queries, not wholly unseen textbook material"})
    m["stages"]["organize"] = "completed"
    for name in ("catalog.json","ku.json","graph.json","query_config.json"):
        m["bindings"].append({"path":str(out/name),"sha256":sha(out/name)})
    write(out / "manifest.json",m)
    print("Frozen catalogue",len(catalogue),"queries",[(u['id'],u['name']) for u in catalogue if u['id'] in queries])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage",choices=("prepare","organize","assemble"))
    p.add_argument("base",type=Path)
    p.add_argument("out",type=Path)
    a=p.parse_args()
    if a.stage=="prepare":
        prepare(a.base.resolve(),a.out.resolve())
    else:
        globals()[a.stage](a.out.resolve())
