"""Version human decisions and rescore frozen predictions without API calls."""
import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from run_ku_prerequisite_phase import audit, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def apply_decisions(reference, decisions, catalog, packet):
    result = copy.deepcopy(reference)
    rows = {(r["prerequisite_ku"], r["target_ku"]): r for r in result["annotations"]}
    packets = {(r["prerequisite"]["id"], r["dependent"]["id"]): r for r in packet["items"]}
    units = {u["id"]: u for u in catalog["knowledge_units"]}
    seen = set()
    for d in decisions["decisions"]:
        pair = (d["prerequisite_ku"], d["target_ku"])
        if pair in seen:
            raise ValueError("Duplicate adjudication")
        seen.add(pair)
        if any(k not in units for k in pair):
            raise ValueError("Unknown actual endpoint")
        if pair not in rows:
            p = packets[pair]
            rows[pair] = {"id": d["id"], "prerequisite_ku": pair[0], "target_ku": pair[1],
                "prerequisite_name": units[pair[0]]["name"], "target_name": units[pair[1]]["name"],
                "evidence": copy.deepcopy(p["source_excerpts"]),
                "target_scope": "Understanding/application, proof mastery not mandatory",
                "selection": "Post-hoc candidate review, not independent recall reference"}
        r = rows[pair]
        if r["id"] != d["id"]:
            raise ValueError("Reference ID mismatch")
        r.update(role=d["role"], reason=d["reason"], provenance="human_user_adjudicated",
                 actual_endpoints_human_approved=True, user_chat_item=d["chat_item"],
                 adjudication_date=decisions["date"])
        r.pop("uncertainty_reason", None)
        if d["role"] == "required":
            r["directness"] = d["directness"]
        else:
            r.pop("directness", None)
    result["annotations"] = list(rows.values())
    result.update(status="Mixed reference: four user-adjudicated pairs, other pairs assistant draft",
                  human_decision_count=len(seen), human_approval_does_not_extend_to_other_pairs=True)
    return result


def run(root, decisions_path, out):
    audit(read(root / "manifest.json"))
    out.mkdir(parents=True, exist_ok=False)
    old = read(root / "reference_draft.json")
    decisions = read(decisions_path)
    updated = apply_decisions(old, decisions, read(root / "endpoint_catalog.json"),
                              read(root / "human_review_packet.json"))
    write(out / "reference.json", updated)
    write(out / "decisions.json", decisions)
    catalog = read(root / "ku.json")["knowledge_units"]
    candidates = read(root / "candidates.json")["candidates"]
    results = {}
    for arm, path in (("production", root / "review_completion01/predictions.json"),
                      ("scoped", root / "scoped_review01/predictions.json")):
        for label, annotations in (("mixed", updated["annotations"]),
                                    ("human_only", [r for r in updated["annotations"] if r["provenance"] == "human_user_adjudicated"] )):
            data = {"endpoints": catalog, "candidates": candidates, "predictions": read(path), "reference": annotations}
            name = f"{arm}_{label}"
            write(out / f"{name}_input.json", data)
            results[name] = score_evaluation(data)
            write(out / f"{name}_scores.json", results[name])
    write(out / "manifest.json", {"date": decisions["date"], "new_api_requests": 0,
        "software_sha256": sha(__file__), "decision_source": str(decisions_path),
        "decision_sha256": sha(decisions_path), "original_reference_sha256": sha(root / "reference_draft.json"),
        "prediction_hashes": {arm: sha(path) for arm, path in (("production", root / "review_completion01/predictions.json"),
                                                               ("scoped", root / "scoped_review01/predictions.json"))},
        "roles": dict(Counter(r["role"] for r in updated["annotations"])),
        "provenance": dict(Counter(r["provenance"] for r in updated["annotations"])),
        "caveat": "Four explicit user decisions only; do not call the full mixed set expert Gold. Post-hoc labels separated from original frozen draft."})
    print(json.dumps({k: {"candidate_recall": v["candidate_direct_recall"], "direct": v["end_to_end_direct"]}
                      for k, v in results.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("decisions", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    run(a.root.resolve(), a.decisions.resolve(), a.out.resolve())
