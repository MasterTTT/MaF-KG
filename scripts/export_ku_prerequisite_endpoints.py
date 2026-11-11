"""Export actual KU members and an unlabeled pair universe, without predictions."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path


def read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def write(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export(root):
    m = read(root / "manifest.json")
    rows = []
    for j in m["jobs"]:
        if j["status"] != "completed":
            raise ValueError("Incomplete endpoint output")
        data = read(Path(j["output"]))
        nodes = {n["id"]: n for n in read(Path(j["plan"]))["nodes"]}
        for u, goal in zip(data["knowledge_units"], data["objective_plans"][j["section"]], strict=True):
            member_ids = [n["id"] for n in u["centers_on"] + u["contains"]]
            if not member_ids or len(member_ids) != len(set(member_ids)):
                raise ValueError("Empty or duplicate member list")
            rows.append({**u, "job_id": j["id"], "goal_id": goal["goal_id"],
                "chapter": j["chapter"], "section": j["section"],
                "cards": [c for c in data["planner_cards"][j["section"]] if c["card_id"] in goal["card_ids"]],
                "full_member_nodes": [nodes[n] for n in member_ids],
                "scope_status": "Actual generated members; understanding/application goal not human-adjudicated"})
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate KU IDs")
    write(root / "endpoint_catalog.json", {"knowledge_units": rows,
          "ku_sha256": hashlib.sha256((root / "ku.json").read_bytes()).hexdigest(),
          "scope": m["scope"]})
    write(root / "pair_universe.json", {"status": "unlabeled universe, NOT negative labels",
        "pairs": [{"prerequisite_ku": a["id"], "target_ku": b["id"], "annotation_status": "unlabeled"}
                  for a, b in itertools.permutations(rows, 2)]})
    lines = ["# 真实KU端点核查", "", "以下为新小标题管线的实际产物，不是已审定Gold。学习目标按理解与应用口径评估，不默认要求证明掌握。", ""]
    for r in rows:
        lines.extend([f"## {r['job_id']}/{r['goal_id']} {r['name']}", f"ID：{r['id']}",
                      f"生成目标：{r['description']}", "中心：" + "；".join(n["name"] for n in r["centers_on"]),
                      "成员：" + "；".join(n["name"] for n in r["contains"])])
        for c in r["cards"]:
            lines.append(f"结构Card：{c['card_id']}；{c['source_path']} L{c['start_line']}–{c['end_line']}")
        for n in r["full_member_nodes"]:
            for e in n.get("evidence", [])[:1]:
                lines.append(f"节点 {n['name']}：{e.get('source_path')} L{e.get('start_line')}–{e.get('end_line')}")
        lines.append("")
    (root / "endpoint_catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Exported {len(rows)} endpoints and {len(rows)*(len(rows)-1)} unlabeled directed pairs")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    export(p.parse_args().root.resolve())
