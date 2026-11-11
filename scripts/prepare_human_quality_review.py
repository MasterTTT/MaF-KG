"""Prepare label-masked review packets without importing previous verdicts."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(experiment, output, seed):
    manifest_path = experiment / "manifest.json"
    manifest = read(manifest_path)
    if manifest["status"] != "completed":
        raise ValueError("Experiment is not completed")
    sections = {s["id"]: s for s in manifest["sections"]}
    jobs = list(manifest["jobs"])
    random.Random(seed).shuffle(jobs)
    packets, mappings = [], []
    for i, job in enumerate(jobs, 1):
        section = sections[job["section"]]
        graph_path = Path(job["directory"]) / "graph.json"
        if job["status"] != "completed" or sha(graph_path).lower() != job["graph_sha256"].lower():
            raise ValueError("Graph completion/hash mismatch")
        source = Path(section["source"])
        if sha(source).lower() != section["source_sha256"].lower():
            raise ValueError("Source changed")
        original = source.read_text(encoding="utf-8").splitlines()
        local = Path(section["input"]).read_text(encoding="utf-8").splitlines()
        if original[section["start_line"] - 1:section["end_line"]] != local:
            raise ValueError("Cannot map section lines to original source")
        graph = read(graph_path)
        code = f"P{i:02d}"
        ids = {n["id"]: f"{code}-N{k:03d}" for k, n in enumerate(graph["nodes"], 1)}
        if len(ids) != len(graph["nodes"]):
            raise ValueError("Duplicate node IDs")

        def evidence(items):
            result = []
            for item in items:
                start, end = item["start_line"], item["end_line"]
                if not 1 <= start <= end <= len(local):
                    raise ValueError("Evidence out of source range")
                result.append({"local_lines": [start, end],
                               "chapter_lines": [start + section["start_line"] - 1,
                                                 end + section["start_line"] - 1],
                               "claimed_quote": item.get("text", ""),
                               "actual_source": "\n".join(local[start - 1:end])})
            return result

        nodes = [{"review_id": ids[n["id"]],
                  "prediction": {k: v for k, v in n.items() if k not in {"id", "evidence"}},
                  "evidence": evidence(n.get("evidence", [])),
                  "decision": {"content_correct": None, "conditions_complete": None,
                               "type_admissible": None, "duplicate_of": [], "reason": ""}}
                 for n in graph["nodes"]]
        relations = []
        for k, r in enumerate(graph["relations"], 1):
            if r["source_id"] not in ids or r["target_id"] not in ids:
                raise ValueError("Missing relation endpoint")
            relations.append({"review_id": f"{code}-R{k:03d}",
                              "prediction": {key: value for key, value in r.items()
                                             if key not in {"id", "source_id", "target_id", "evidence"}},
                              "source": ids[r["source_id"]], "target": ids[r["target_id"]],
                              "evidence": evidence(r.get("evidence", [])),
                              "decision": {"endpoints_admissible": None, "type_correct": None,
                                           "direction_correct": None, "source_supported": None,
                                           "reason": ""}})
        packets.append({"packet_id": code, "section": section["title"],
                        "reviewer": {"name": "", "previously_seen_output": None},
                        "nodes": nodes, "relations": relations})
        mappings.append({"packet_id": code, "arm": job["name"], "repeat": job["repeat"],
                         "section": job["section"], "graph_path": str(graph_path),
                         "graph_sha256": sha(graph_path), "node_mapping": ids,
                         "relation_mapping": {f"{code}-R{k:03d}": r["id"]
                                              for k, r in enumerate(graph["relations"], 1)}})
    output.mkdir(parents=True, exist_ok=False)
    (output / "private").mkdir()
    for packet in packets:
        write(output / (packet["packet_id"] + ".json"), packet)
        lines = [f"# {packet['packet_id']}：{packet['section']}", "",
                 "隐藏方法标签复核；不是新的盲测。判定可用是/否/不确定，需写理由。", ""]
        for kind in ("nodes", "relations"):
            for item in packet[kind]:
                lines.extend([f"## {item['review_id']}", "", "```json",
                              json.dumps({k: v for k, v in item.items() if k != "decision"},
                                         ensure_ascii=False, indent=2), "```", "",
                              "判定：", json.dumps(item["decision"], ensure_ascii=False), ""])
        (output / (packet["packet_id"] + ".md")).write_text("\n".join(lines), encoding="utf-8")
    write(output / "private" / "mapping.json", {"seed": seed, "packets": mappings})
    write(output / "manifest.json", {"experiment_manifest_sha256": sha(manifest_path),
                                     "generator_sha256": sha(__file__), "packets": len(packets),
                                     "nodes": sum(len(p["nodes"]) for p in packets),
                                     "relations": sum(len(p["relations"]) for p in packets),
                                     "status": "prepared; no human decisions yet"})
    (output / "README.md").write_text(
        "# 人工复核包\n\nP01–P12隐藏方法标签和旧评分，包含完整预测与证据。"
        "教材行号已核对原文映射。JSON用于记录，Markdown用于阅读。\n\n"
        "不要向评审者提供private目录。填写评审者身份及是否看过输出；"
        "无法判断时用不确定，不将缺失证据直接等同数学错误。"
        "节点准入、正文正确性和条件完整性分别判定；关系也分别判断端点、类型、方向与出处。"
        "duplicate_of填写本包节点编号。所有判定初始为空，未复制助手旧意见。\n\n"
        "这12份是三个开发小节的两次配对，不是12个独立教材样本，"
        "也不是KU复核包。此包尚未产生人工分数。\n", encoding="utf-8")
    return packets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=20261004)
    args = parser.parse_args()
    result = prepare(args.experiment, args.output, args.seed)
    print(f"Prepared {len(result)} packets")
