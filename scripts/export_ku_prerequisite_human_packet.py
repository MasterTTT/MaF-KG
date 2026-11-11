"""Prepare original-source review cards; hide reference and model verdicts."""
import argparse
import json
from pathlib import Path

from run_ku_prerequisite_phase import read, sha, write


def export(root):
    catalog = {u["id"]: u for u in read(root / "endpoint_catalog.json")["knowledge_units"]}
    graph = {n["id"]: n for n in read(root / "graph.json")["nodes"]}
    pool = read(root / "candidates.json")["candidates"]
    refs = read(root / "reference_draft.json")["annotations"]
    pairs = {(r["target_ku"], r["prerequisite_ku"]): set() for r in refs}
    for c in pool:
        key = (c["source_ku"], c["target_ku"])
        ids = pairs.setdefault(key, set())
        for s in c["signals"]:
            for e in s["edges"]:
                ids.update((e["source_id"], e["target_id"]))
        for chain in c["chain_via"]:
            ids.update(chain["hop1"] + chain["hop2"])
    sources = {p.name: p for p in (root / "snapshot/input").glob("*.md")}
    rows = []

    def endpoint(u):
        return {"id": u["id"], "name": u["name"], "goal": u["description"],
                "centers": [n["name"] for n in u["centers_on"]],
                "members": [n["name"] for n in u["contains"]]}
    for number, ((dependent, prerequisite), related_nodes) in enumerate(sorted(pairs.items()), 1):
        excerpts = []
        seen = set()
        ids = list(sorted(related_nodes)) + [n["id"] for n in catalog[dependent]["centers_on"] + catalog[prerequisite]["centers_on"]]
        for nid in dict.fromkeys(ids):
            if nid not in graph:
                continue
            for e in graph[nid].get("evidence", [])[:2]:
                basename = Path(e.get("source_path", "")).name
                path = sources.get(basename)
                start, end = e.get("start_line"), e.get("end_line")
                if path is None or type(start) is not int or type(end) is not int:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
                if not 1 <= start <= end <= len(lines) or (basename, start, end) in seen:
                    continue
                seen.add((basename, start, end))
                quote = "\n".join(lines[start-1:end])
                excerpts.append({"source": basename, "source_sha256": sha(path), "start_line": start,
                    "end_line": end, "related_node": graph[nid]["name"], "quote": quote[:1000],
                    "excerpt_truncated": len(quote) > 1000})
        rows.append({"item_id": f"H{number:02d}", "dependent": endpoint(catalog[dependent]),
                     "prerequisite": endpoint(catalog[prerequisite]), "source_excerpts": excerpts[:6],
                     "human_role": None, "human_directness": None, "human_reason": None})
    write(root / "human_review_packet.json", {"status": "Pending human review; no prediction or draft labels displayed",
          "selection": "Union of 19 pre-prediction reference pairs and 21 generated candidates; candidate-based additions are post-hoc, not independent recall Gold",
          "scope": "Understanding/application, not proof mastery", "items": rows})
    lines = ["# 人工先修核查材料", "", "本材料隐藏模型预测和草案标签，但条目集合包含候选生成后的补充项，不是严格双盲或独立召回Gold。", "",
             "每项请判断：直接必需 / 间接必需 / 原理支持 / 证明支持 / 非必需 / 不确定。默认目标为理解与应用，不要求掌握完整证明。", ""]
    for r in rows:
        a, b = r["dependent"], r["prerequisite"]
        lines.extend([f"## {r['item_id']} 学习{a['name']}是否必须先掌握{b['name']}？",
                      f"目标：{a['goal']}", f"前置候选目标：{b['goal']}",
                      "目标中心：" + "；".join(a["centers"]), "前置候选中心：" + "；".join(b["centers"]),
                      "目标其他成员：" + "；".join(a["members"]), "前置候选其他成员：" + "；".join(b["members"])])
        for e in r["source_excerpts"]:
            lines.append(f"出处：{e['source']} L{e['start_line']}–{e['end_line']}（{e['related_node']}）")
            lines.extend(["```text", e["quote"], "```"])
            if e["excerpt_truncated"]:
                lines.append("摘录较长，以上只显示前1000字符，完整原文在冻结教材范围内。")
        lines.extend(["人工裁决：待填写。", ""])
    (root / "human_review_packet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Prepared {len(rows)} source-backed human review cards; predictions hidden")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    export(p.parse_args().root.resolve())
