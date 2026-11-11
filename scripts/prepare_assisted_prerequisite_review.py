"""Freeze union review packets; do not expose model verdicts as annotation cues."""
import argparse
from pathlib import Path

from run_ku_prerequisite_phase import read, sha, write


def prepare(base, retrieval, out):
    out.mkdir(parents=True, exist_ok=False)
    catalog = {u["id"]: u for u in read(base / "endpoint_catalog.json")["knowledge_units"]}
    reference = read(retrieval / "adjudication_v2/reference.json")
    sources = {p.name: p for p in (base / "snapshot/input").glob("*.md")}
    pool = {}
    paths = [base / "endpoint_catalog.json", retrieval / "adjudication_v2/reference.json"]
    for label, path in (("original", base / "candidates.json"),
                        ("bm25", retrieval / "run01/candidates.json"),
                        ("semantic", retrieval / "semantic01/candidates.json")):
        paths.append(path)
        for c in read(path)["candidates"]:
            pool.setdefault((c["source_ku"], c["target_ku"]), set()).add(label)
    for r in reference["annotations"]:
        pool.setdefault((r["target_ku"], r["prerequisite_ku"]), set())
    records = []

    def endpoint(u):
        excerpts = []
        seen = set()
        for n in u["full_member_nodes"]:
            for e in n.get("evidence", [])[:2]:
                name = Path(e.get("source_path", "")).name
                p = sources.get(name)
                start, end = e.get("start_line"), e.get("end_line")
                if p is None or type(start) is not int or type(end) is not int or (name, start, end) in seen:
                    continue
                lines = p.read_text(encoding="utf-8").splitlines()
                if not 1 <= start <= end <= len(lines):
                    continue
                seen.add((name, start, end))
                text = "\n".join(lines[start-1:end])
                excerpts.append({"source": str(p), "start_line": start, "end_line": end,
                                 "member": n["name"], "text": text[:2000], "truncated": len(text)>2000})
        return {"id": u["id"], "name": u["name"], "goal": u["description"],
                "centers_on": u["centers_on"], "contains": u["contains"],
                "full_member_nodes": u["full_member_nodes"], "source_excerpts": excerpts}
    for i, ((a, b), origins) in enumerate(sorted(pool.items()), 1):
        records.append({"item_id": f"S{i:02d}", "dependent": endpoint(catalog[a]),
                        "prerequisite": endpoint(catalog[b]), "selection_origins": sorted(origins)})
    write(out / "packet.json", {"items": records, "predictions_included": False,
        "scope": "User-supervised assistant source review, not independent expert or double-blind annotation"})
    write(out / "protected_user_decisions.json", {"annotations": [r for r in reference["annotations"]
                                                               if r["provenance"] == "human_user_adjudicated"]})
    paths.extend(sources.values())
    write(out / "manifest.json", {"status": "prepared", "review_process": "user-supervised AI-assisted source review",
        "reviewer": "Codex assistant under user authorization", "user_confirmed_pairs": 5,
        "no_new_model_experiment": True, "sources": [{"path": str(p), "sha256": sha(p)} for p in paths],
        "packet_sha256": sha(out / "packet.json"), "software_sha256": sha(__file__)})
    print(f"Frozen {len(records)} review pairs; {sum(bool(r['selection_origins']) for r in records)} are generated candidates")
    for r in records:
        print(r["item_id"], r["dependent"]["name"], "depends_on", r["prerequisite"]["name"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base", type=Path)
    p.add_argument("retrieval", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    prepare(a.base.resolve(), a.retrieval.resolve(), a.out.resolve())
