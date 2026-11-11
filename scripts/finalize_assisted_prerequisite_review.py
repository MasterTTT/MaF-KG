"""Lock source-assisted review, rescore native outputs, export curated local views."""
import argparse
import copy
import re
from collections import Counter
from pathlib import Path

import networkx as nx

from run_ku_prerequisite_phase import read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def validate_decisions(packet, decisions, protected):
    items = {r["item_id"]: r for r in packet["items"]}
    rows = decisions["decisions"]
    if len(rows) != len({r["item_id"] for r in rows}) or {r["item_id"] for r in rows} != set(items):
        raise ValueError("Review must cover every packet item exactly once")
    locked = {(r["target_ku"], r["prerequisite_ku"]): r for r in protected["annotations"]}
    for d in rows:
        item = items[d["item_id"]]
        pair = (item["dependent"]["id"], item["prerequisite"]["id"])
        if d["role"] not in {"required", "proof_support", "principle_support", "related_or_none", "uncertain"}:
            raise ValueError("Invalid source-review role")
        if d["role"] == "required" and d.get("directness") not in {"direct", "indirect"}:
            raise ValueError("Required judgment missing directness")
        if d["role"] != "required" and d.get("directness") is not None:
            raise ValueError("Nonrequired judgment has directness")
        if pair in locked:
            old = locked[pair]
            if (d["role"], d.get("directness")) != (old["role"], old.get("directness")):
                raise ValueError("Attempted to override explicit user decision")
    return items, locked


def execute(base, retrieval, out):
    m = read(out / "manifest.json")
    if m["status"] != "prepared":
        raise ValueError("No overwrite of finalized review")
    for b in m["sources"]:
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("Frozen review source changed")
    if sha(out / "packet.json") != m["packet_sha256"]:
        raise ValueError("Review packet changed")
    packet = read(out / "packet.json")
    decisions = read(out / "source_review_decisions.json")
    items, locked = validate_decisions(packet, decisions, read(out / "protected_user_decisions.json"))
    source_map = {"ch2": "第二章", "ch3": "第三章", "ch4": "第四章", "ch7": "第七章"}
    references = []
    for d in decisions["decisions"]:
        item = items[d["item_id"]]
        dep, pre = item["dependent"], item["prerequisite"]
        pair = (dep["id"], pre["id"])
        r = copy.deepcopy(locked[pair]) if pair in locked else {
            "id": d["item_id"], "prerequisite_ku": pre["id"], "target_ku": dep["id"],
            "prerequisite_name": pre["name"], "target_name": dep["name"],
            "role": d["role"], "reason": d["reason"], "provenance": "assistant_reviewed_under_user_authorization",
            "target_scope": "Understanding and application of the actual KU objective, not proof mastery"}
        if d["role"] == "required":
            r["directness"] = d["directness"]
        r.update(review_item_id=d["item_id"], source_review_reason=d["reason"], source_review_citations=[])
        if d.get("required_scope"):
            r["required_scope"] = d["required_scope"]
        for citation in d["citations"]:
            match = re.fullmatch(r"(ch[2347]):(\d+)-(\d+)", citation)
            if not match:
                raise ValueError("Malformed source citation")
            chapter, start, end = match[1], int(match[2]), int(match[3])
            path = base / "snapshot/input" / (source_map[chapter] + "_全章_无习题.md")
            lines = path.read_text(encoding="utf-8").splitlines()
            if not 1 <= start <= end <= len(lines):
                raise ValueError("Citation outside frozen source")
            r["source_review_citations"].append({"source_path": str(path), "sha256": sha(path),
                "start_line": start, "end_line": end, "text": "\n".join(lines[start-1:end])})
        references.append(r)
    write(out / "reference.json", {"status": "Completed user-supervised assisted review, not independently expert-annotated Gold",
          "annotations": references, "human_direct_judgments": 5, "assistant_delegated_judgments": 43,
          "annotation_after_prediction": True, "known_prediction_access": "Coordinator previously saw system outputs; no strict blind claim",
          "unlisted_pairs": "Unlabeled, not negatives", "review_scope_policy": decisions["scope_policy"]})
    catalog = read(base / "endpoint_catalog.json")["knowledge_units"]
    endpoints = read(base / "ku.json")["knowledge_units"]
    arms = {"original_production": (base / "candidates.json", base / "review_completion01/predictions.json"),
            "scope_baseline": (base / "candidates.json", base / "scoped_review01/predictions.json"),
            "bm25": (retrieval / "run01/candidates.json", retrieval / "run01/predictions.json"),
            "semantic": (retrieval / "semantic01/candidates.json", retrieval / "semantic01/predictions.json")}
    result = {}
    for name, (cpath, ppath) in arms.items():
        data = {"endpoints": endpoints, "candidates": read(cpath)["candidates"],
                "predictions": read(ppath), "reference": references}
        write(out / f"{name}_input.json", data)
        scores = score_evaluation(data)
        write(out / f"{name}_scores.json", scores)
        result[name] = {"candidate_recall": scores["candidate_direct_recall"], "direct": scores["end_to_end_direct"],
                        "scope": scores["scope"], "prediction_sha256": sha(ppath)}
    edges, support = [], []
    for r in references:
        record = {"source_ku": r["target_ku"], "target_ku": r["prerequisite_ku"],
                  "source_name": r["target_name"], "target_name": r["prerequisite_name"],
                  "audit": {"review_item_id": r["review_item_id"], "provenance": r["provenance"],
                            "reason": r["source_review_reason"], "required_scope": r.get("required_scope"),
                            "citations": r["source_review_citations"]}}
        if r["role"] == "required" and r["directness"] == "direct":
            edges.append(record)
        elif r["role"] in {"proof_support", "principle_support"}:
            support.append({**record, "review_role": r["role"]})
    graph = nx.DiGraph()
    graph.add_nodes_from(u["id"] for u in endpoints)
    graph.add_edges_from((e["source_ku"], e["target_ku"]) for e in edges)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("Curated prerequisite graph has cycles; inspect instead of auto-deleting")
    reduced = nx.transitive_reduction(graph)
    kept = [e for e in edges if reduced.has_edge(e["source_ku"], e["target_ku"])]
    same_reachability = all(nx.descendants(graph, n) == nx.descendants(reduced, n) for n in graph)
    if not same_reachability:
        raise ValueError("Reduction altered reachability")
    metadata = {"scope": "Local 20-KU assisted-reviewed prototype; no production import",
                "source_ku_path": str(base / "ku.json"), "source_ku_sha256": sha(base / "ku.json"),
                "review_reference_sha256": sha(out / "reference.json"), "knowledge_units": endpoints}
    write(out / "reviewed_depends_on.json", {**metadata, "depends_on": edges})
    write(out / "reviewed_depends_on_reduced.json", {**metadata, "depends_on": kept,
        "removed_edges": [e for e in edges if not reduced.has_edge(e["source_ku"], e["target_ku"])],
        "reachability_preserved": same_reachability,
        "note": "Navigation reduction only; full direct evidence and scope remain in unreduced view"})
    write(out / "reviewed_support_links.json", {**metadata, "support_links": support,
        "note": "Review annotations, not new production relation types or required learning paths"})
    write(out / "comparison.json", {"arms": result, "review_roles": dict(Counter(r["role"] for r in references)),
          "review_provenance": dict(Counter(r["provenance"] for r in references)),
          "direct_edges": len(edges), "reduced_edges": len(kept), "support_links": len(support),
          "reachability_preserved": same_reachability, "new_api_requests": 0})
    lines = ["# 先修关系辅助人工审查", "", "用户直接裁决5项，授权助手源文审查43项。不是双专家独立标注或严格盲测。", "",
             "|编号|目标|前置候选|审查角色|范围/理由|", "|---|---|---|---|---|"]
    for r in references:
        reason = r.get("required_scope", "") + " " + r["source_review_reason"]
        lines.append(f"|{r['review_item_id']}|{r['target_name']}|{r['prerequisite_name']}|{r['role']}|{reason.replace('|','/')}|")
    (out / "review_table.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    m.update(status="completed", review_decisions_sha256=sha(out / "source_review_decisions.json"),
             reference_sha256=sha(out / "reference.json"), new_api_requests=0, human_direct_pairs=5,
             assistant_delegated_pairs=43, direct_edges=len(edges), reduced_edges=len(kept), support_links=len(support))
    write(out / "manifest.json", m)
    write(out / "artifact_index.json", {"files": [{"path": str(p), "sha256": sha(p)} for p in out.iterdir()
                                                  if p.is_file() and p.name != "artifact_index.json"]})
    print({"roles": dict(Counter(r["role"] for r in references)), "curated_direct": len(edges),
           "reduced": len(kept), "support": len(support)})
    print({k: v["direct"] for k, v in result.items()})


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base", type=Path)
    p.add_argument("retrieval", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    execute(a.base.resolve(), a.retrieval.resolve(), a.out.resolve())
