"""Bind the pre-existing source draft to actual KUs before seeing new candidates.

This is an assistant reference draft, NOT expert Gold. Four approved semantic
decisions do not imply approval of the generated endpoint members. Unresolved
capability scopes stay uncertain; absent anchors stay excluded, not negative.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def write(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def freeze(out):
    if (out / "candidates.json").exists() or (out / "reference_draft.json").exists():
        raise ValueError("Reference must be frozen before candidates; no overwrite")
    root = Path(__file__).resolve().parents[1]
    original = root / "evaluation/prospective_2026-10-04/prerequisite_draft_v2/candidate_pool.json"
    copy = out / "source_candidate_draft.json"
    shutil.copy2(original, copy)
    pool = read(copy)
    catalog = read(out / "endpoint_catalog.json")["knowledge_units"]
    by_slot = {(u["job_id"], u["goal_id"]): u for u in catalog}
    slots = {"T": ("ch3_s3", "g1"), "TP": ("ch3_s3", "g1"), "I": ("ch4_s3", "g1"),
             "O1": ("ch7_s5", "g1"), "O2": ("ch7_s5", "g2"), "O3": ("ch7_s5", "g3"),
             "D": ("ch2_s3", "g1"), "A": ("ch4_s1", "g1"), "PR": ("ch2_s2", "g1"),
             "CR": ("ch2_s2", "g3"), "S": ("ch7_s2", "g1"),
             "LH": ("ch3_s2", "g1"), "CM": ("ch3_s1", "g3"), "LIN": ("ch7_s4", "g1")}
    anchors = {key: by_slot[slot] for key, slot in slots.items()}
    bindings = {b["id"]: b for b in pool["bindings"]}
    evidence = {e["id"]: e for e in pool["evidence"]}
    source_rows = {"ch2": "第二章", "ch3": "第三章", "ch4": "第四章", "ch7": "第七章"}
    annotated, excluded = [], []
    for c in pool["candidates"]:
        if c["target_anchor"] not in anchors or c["prerequisite_anchor"] not in anchors:
            excluded.append({"id": c["id"], "target_anchor": c["target_anchor"],
                "prerequisite_anchor": c["prerequisite_anchor"],
                "reason": "No actual frozen KU for this anchor; conditional generic solvers and absent Q/SUB excluded, not negatives."})
            continue
        source, target = anchors[c["prerequisite_anchor"]], anchors[c["target_anchor"]]
        excerpts = []
        for eid in c["evidence_ids"]:
            e = evidence[eid]
            b = bindings[e["source"]]
            original_path = Path(b["path"])
            if hashlib.sha256(original_path.read_bytes()).hexdigest() != b["sha256"]:
                raise ValueError("Source changed since independent draft")
            chapter = source_rows[e["source"]]
            path = out / "snapshot/input" / (chapter + "_全章_无习题.md")
            lines = path.read_text(encoding="utf-8").splitlines()
            excerpt = "\n".join(lines[e["line_start"]-1:e["line_end"]])
            if e["quote"] not in excerpt:
                raise ValueError(f"Exact draft excerpt does not match {eid}")
            excerpts.append({"id": eid, "source_path": str(path), "start_line": e["line_start"],
                             "end_line": e["line_end"], "quote": e["quote"]})
        if c["id"] in {"P4", "P7", "P10"}:
            role = "uncertain"
        elif c["label"] == "positive":
            role = "required"
        elif c["label"] == "negative":
            role = "related_or_none"
        elif c["id"] == "P3":
            role = "principle_support"
        elif c["label"] == "proof_support":
            role = "proof_support"
        else:
            raise ValueError("Unexpected role in finite draft")
        row = {"id": c["id"], "prerequisite_ku": source["id"], "target_ku": target["id"],
            "prerequisite_name": source["name"], "target_name": target["name"], "role": role,
            "provenance": "assistant_draft", "reason": c["reason"], "evidence": excerpts,
            "target_scope": "Understanding and application, proof mastery not mandatory",
            "original_anchor_goal": c["target_anchor"], "original_anchor_prerequisite": c["prerequisite_anchor"],
            "prior_user_semantic_decision": c["approval"], "actual_endpoints_human_approved": False}
        if role == "required":
            row["directness"] = "direct"
        if role == "uncertain":
            row["uncertainty_reason"] = ("P4/P10: concept KU does not guarantee all required integral computation; "
                "P7: confirm reconstruction scope and conditional/zero branches. These remain unadjudicated.")
        annotated.append(row)
    if len({(r["prerequisite_ku"], r["target_ku"]) for r in annotated}) != len(annotated):
        raise ValueError("Mapping merged distinct reference pairs; resolve before freezing")
    data = {"status": "frozen assistant source-review draft, NOT expert Gold",
        "provenance": "Coordinator bound source-backed 2026-10-04 draft before new candidate generation. Source-review subagent stopped without artifacts; no independent-human agreement claimed.",
        "source_pool_sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
        "ku_sha256": hashlib.sha256((out / "ku.json").read_bytes()).hexdigest(),
        "source_quote_validation": "All used excerpts matched the frozen source lines exactly",
        "annotations": annotated, "excluded": excluded,
        "unlisted_pairs": "Unlabeled, not negatives", "all_actual_endpoint_mappings": "assistant draft"}
    write(out / "reference_draft.json", data)
    lines = ["# 有限真实KU先修审阅草案", "", "不是专家Gold；原有四项语义批准不等于实际KU成员映射获批。以下在本轮候选生成前固定。", ""]
    for r in annotated:
        lines.extend([f"## {r['id']} {r['prerequisite_name']} → {r['target_name']}",
                      f"草案角色：{r['role']}；理由：{r['reason']}"])
        for e in r["evidence"]:
            lines.append(f"{Path(e['source_path']).name} L{e['start_line']}–{e['end_line']}：{e['quote']}")
        if r["role"] == "uncertain":
            lines.append("待裁决：" + r["uncertainty_reason"])
        lines.append("")
    lines.extend(["## 范围外项目", json.dumps(excluded, ensure_ascii=False, indent=2), ""])
    (out / "reference_draft.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Frozen {len(annotated)} draft pairs; excluded {len(excluded)} absent/conditional anchors")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out", type=Path)
    freeze(p.parse_args().out.resolve())
