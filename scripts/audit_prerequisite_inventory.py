"""Read-only historical prerequisite inventory and deterministic review queue."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict, deque
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reachable(adjacency: dict, start: str, end: str, omitted: tuple | None = None) -> bool:
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for target in adjacency.get(current, ()):
            if (current, target) == omitted:
                continue
            if target == end:
                return True
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return False


def inventory(data: dict, units: dict) -> tuple[dict, list[dict]]:
    edges = data["depends_on"]
    pairs = [(e["source_ku"], e["target_ku"]) for e in edges]
    adjacency = defaultdict(set)
    for source, target in pairs:
        adjacency[source].add(target)
    audits = {(e["source_ku"], e["target_ku"]): e.get("audit", {})
              for e in data.get("audit", {}).get("reviewed_edges", [])}
    rows = []
    for source, target in sorted(set(pairs)):
        row = {"source_ku": source, "target_ku": target,
               "source_name": units.get(source, {}).get("name", source),
               "target_name": units.get(target, {}).get("name", target),
               "audit": audits.get((source, target)),
               "self_loop": source == target,
               "cycle_edge": source != target and reachable(adjacency, target, source),
               "alternative_path": reachable(adjacency, source, target, (source, target))}
        rows.append(row)
    return {"edge_records": len(edges), "unique_pairs": len(set(pairs)),
            "duplicate_records": len(edges) - len(set(pairs)),
            "missing_endpoint_pairs": sum(s not in units or t not in units for s, t in set(pairs)),
            "incident_units": len({n for p in pairs for n in p}),
            "units_without_incident_edges": len(set(units) - {n for p in pairs for n in p}),
            "self_loops": sum(r["self_loop"] for r in rows),
            "cycle_edges": sum(r["cycle_edge"] for r in rows),
            "pairs_with_alternative_path": sum(r["alternative_path"] for r in rows),
            "review_flags": dict(Counter(
                "unknown" if r["audit"] is None else
                "reviewed" if r["audit"].get("reviewed") else "not_reviewed"
                for r in rows))}, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kg-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    unit_path = args.kg_root / "全书七章_knowledge_units_merged.json"
    units_data = read(unit_path)
    units = {u["id"]: u for u in units_data["knowledge_units"]}
    files = {"historical_merged": args.kg_root / "全书七章_ku_depends_merged.json",
             "historical_reviewed": args.kg_root / "全书七章_ku_depends_llm_reviewed_v1.json"}
    graphs = [Path(p) for p in units_data["source_graphs"]]
    node_index = {}
    for path in graphs:
        for node in read(path)["nodes"]:
            node_index.setdefault(node["id"], node)
    metrics = {}
    rows = {}
    for label, path in files.items():
        metrics[label], rows[label] = inventory(read(path), units)
    buckets = defaultdict(list)
    for row in rows["historical_reviewed"]:
        audit = row["audit"]
        flag = "unknown" if audit is None else "reviewed" if audit.get("reviewed") else "not_reviewed"
        buckets[(flag, row["alternative_path"], row["cycle_edge"])].append(row)
    selected = []
    while len(selected) < 12 and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(selected) < 12:
                selected.append(buckets[key].pop(0))
    queue = []
    for number, row in enumerate(selected, 1):
        record = {**row, "item_id": f"P{number:02}", "human_verdict": None,
                  "status": "unjudged diagnostic sample, not Gold", "endpoints": {}}
        for role in ("source", "target"):
            unit = units[row[f"{role}_ku"]]
            anchors = []
            for member in unit.get("centers_on", []) + unit.get("contains", []):
                node = node_index.get(member["id"])
                if node:
                    anchors.append({"id": node["id"], "name": node["name"],
                                    "description": node.get("description", ""),
                                    "evidence": node.get("evidence", [])})
            record["endpoints"][role] = {"description": unit.get("description", ""),
                                         "evidence": unit.get("evidence", []), "nodes": anchors}
        queue.append(record)
    paths = [unit_path, *files.values(), *graphs]
    manifest = {"status": "historical inventory; no new pipeline or API run",
                "direction": "source learning depends on target learning",
                "sampling": "sorted round-robin across review/alternative-path/cycle flags; diagnostic, not random",
                "inputs": [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in paths], "metrics": metrics,
                "limits": "Alternative paths do not establish semantic redundancy. Historical KU IDs cannot transfer to new heading KUs."}
    for name, value in (("manifest.json", manifest), ("edge_inventory.json", rows), ("review_queue.json", queue)):
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 历史先修关系审定候选", "", "方向：源KU的学习依赖目标KU。以下为诊断抽样，不是Gold或准确率。", ""]
    for item in queue:
        lines.extend([f"## {item['item_id']} {item['source_name']} → {item['target_name']}",
                      f"历史审核标记：{item['audit']}；替代路径：{item['alternative_path']}；环路边：{item['cycle_edge']}",
                      "待裁决：直接先修 / 间接先修 / 仅相关 / 反向 / 不确定。"])
        for role, title in (("source", "源"), ("target", "目标")):
            endpoint = item["endpoints"][role]
            lines.append(f"{title}KU：{endpoint['description']}")
            for ev in endpoint["evidence"]:
                lines.append(f"教材范围：{ev.get('source_path')} L{ev.get('start_line')}–{ev.get('end_line')}")
            for node in endpoint["nodes"][:3]:
                lines.append(f"节点锚点：{node['name']}：{node['description']}")
                for ev in node["evidence"][:1]:
                    lines.append(f"出处：{ev.get('source_path')} L{ev.get('start_line')}–{ev.get('end_line')}；原文：{ev.get('text', '')}")
        lines.append("")
    (args.out / "review_queue.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
