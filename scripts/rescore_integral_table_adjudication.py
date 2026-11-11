"""Append one explicit user decision and rescore unchanged retrieval outputs."""
import argparse
import copy
from pathlib import Path

from run_ku_prerequisite_phase import read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def run(root, out):
    out.mkdir(parents=True, exist_ok=False)
    decision_path = root / "user_decision_integral_table_2026-10-07.json"
    decision = read(decision_path)
    original = root / "semantic01/snapshot/reference.json"
    ref = copy.deepcopy(read(original))
    pair = (decision["prerequisite_ku"], decision["target_ku"])
    if any((r["prerequisite_ku"], r["target_ku"]) == pair for r in ref["annotations"]):
        raise ValueError("Do not silently replace an existing reference judgment")
    source = root.parents[0] / "prerequisite_real_ku_2026-10-05/run02/snapshot/input/第七章_全章_无习题.md"
    lines = source.read_text(encoding="utf-8").splitlines()
    for e in decision["evidence"]:
        if e["quote"] not in "\n".join(lines[e["start_line"]-1:e["end_line"]]):
            raise ValueError("Source excerpt mismatch")
    ref["annotations"].append(decision)
    ref.update(status="Mixed reference: five user-adjudicated pairs, sixteen assistant drafts",
               human_decision_count=5)
    write(out / "reference.json", ref)
    write(out / "decision.json", decision)
    arms = {"baseline": root.parent / "prerequisite_real_ku_2026-10-05/adjudicated_2026-10-07/run01/scoped_mixed_input.json",
            "bm25": root / "run01/mixed_input.json", "semantic": root / "semantic01/mixed_input.json"}
    results = {}
    for name, p in arms.items():
        data = read(p)
        data["reference"] = ref["annotations"]
        write(out / f"{name}_input.json", data)
        scores = score_evaluation(data)
        write(out / f"{name}_scores.json", scores)
        results[name] = scores["end_to_end_direct"]
    write(out / "manifest.json", {"decision_sha256": sha(decision_path), "original_reference_sha256": sha(original),
        "source_sha256": sha(source), "new_api_requests": 0, "original_predictions_unchanged": True,
        "input_hashes": {n: sha(p) for n, p in arms.items()}, "direct_metrics": results,
        "caveat": "Post-hoc user adjudication, not new model behavior or a complete expert Gold"})
    print(results)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    run(a.root.resolve(), a.out.resolve())
