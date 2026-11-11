"""Flag existing exact native edges that a review has graded below full coverage."""
import argparse
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def contradictions(packet, review):
    items = {i["item_id"]: i for i in packet["items"]}
    candidates = {
        row["reference_id"]: {
            items[key]["native"].get("id") for key in row["item_ids"]
            if items[key]["kind"] == "entity" and isinstance(items[key]["native"], dict)
        } for row in review["node_coverage"]
    }
    references = {r["id"]: r for r in packet["reference"]["relations"]}
    findings = []
    for row in review["relation_coverage"]:
        if row["status"] == "full":
            continue
        reference = references[row["reference_id"]]
        sources = candidates[reference["start"]] - {None}
        targets = candidates[reference["end"]] - {None}
        hits = [i["item_id"] for i in packet["items"]
                if i["kind"] == "assertion" and isinstance(i["native"], dict)
                and i["native"].get("type") == reference["type"]
                and i["native"].get("source_id") in sources
                and i["native"].get("target_id") in targets]
        if hits:
            findings.append({"reference_id": row["reference_id"], "review_status": row["status"],
                             "exact_native_edges": hits, "reason": row["reason"]})
    return findings


def audit(directory):
    findings = []
    for job in read(directory / "mapping_private.json"):
        if job["status"] != "completed" or job["method"] not in {"maf", "direct"}:
            continue
        label = job["label"]
        rows = contradictions(read(directory / f"{label}.json"), read(directory / f"{label}_review.json"))
        findings.extend({"label": label, **row} for row in rows)
    return {"findings": findings,
            "scope": "Exact-edge check against reviewer-declared node candidates; not a semantic grader",
            "limit": "Does not judge absent edges, triples, source support, conditions, or equivalent subgraphs"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["findings"] else 0)
