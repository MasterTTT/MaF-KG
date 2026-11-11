"""Check assistant review hashes and recompute strictly typed reference hits."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ("run01_deepseek_direct", "run02_deepseek_sequential", "run02_gpt6_direct")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(directory):
    review = read(directory / "review.json")
    for binding in review["scope"]["sha256_bindings"]:
        path = Path(binding["path"])
        if not path.is_absolute():
            path = ROOT / path
        if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
            raise ValueError("Review source/graph/reference hash mismatch")
    rows = []
    for label, relative in zip(LABELS, review["scope"]["reviewed_graphs_only"]):
        graph = read(ROOT / relative)
        nodes = {n["id"]:n for n in graph["nodes"]}
        identities, self_contained, checklist = {}, 0, 0
        for item in review["mandatory_items"]:
            annotation = item["annotations"][label]
            identity = annotation["identity_id"]
            carriers = annotation["content_ids"]
            if any(i not in nodes for i in carriers):
                raise ValueError("Unknown content carrier")
            if identity is not None:
                if identity not in carriers or nodes[identity]["type"] != item["type"]:
                    raise ValueError("Wrong-type identity or missing identity carrier")
                if identity in identities.values():
                    raise ValueError("Identity alignment is not one-to-one")
                identities[item["id"]] = identity
            self_contained += annotation["description_self_contained"]
            checklist += annotation["source_checklist_complete"]
        triples = {(r["source_id"],r["type"],r["target_id"]) for r in graph["relations"]}
        hits = 0
        for relation in review["required_relations"]:
            expected = (identities.get(relation["source_item"]),relation["type"],
                        identities.get(relation["target_item"]))
            hit = expected in triples
            if hit != relation["strict_checks"][label]["strict_hit"]:
                raise ValueError("Annotated relation hit differs from actual typed triple")
            hits += hit
        rows.append({"label":label,"required":len(review["mandatory_items"]),
                     "identity_hits":len(identities),"finite_relation_hits":hits,
                     "finite_relation_total":len(review["required_relations"]),
                     "self_contained_descriptions":self_contained,
                     "reviewer_combined_carrier_checklist_hits":checklist})
    result = {"reviewer":review["reviewer"],"status":"mechanically validated assistant annotations; not independent human evaluation",
              "rows":rows,"caveat":"Combined-carrier checklist coverage may require reading multiple nodes or a stated specialization; it is not self-contained node completeness."}
    (directory / "validated_summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.directory.resolve()),ensure_ascii=False,indent=2))
