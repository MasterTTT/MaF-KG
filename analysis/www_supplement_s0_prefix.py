"""S0.9 export the independent prefix inputs for E1p and freeze the common pool.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 3.4.

Input I (第四章 L5-L1126) is cut into three nested ranges:

  I1  L5-L375   (第一节)
  I2  L5-L947   (第一、二节)
  I3  L5-L1126  (all three sections; the E1 input, already exported)

Direct and MaF each run twice on I1 and I2. I3 reuses the E1 runs, so E1p adds
8 method runs, not 12.

Evaluation uses ONE frozen common pool: the reference items of input I whose
source evidence lies entirely inside I1. Every range is scored against that same
pool, so the denominator never changes and the trajectory is comparable. The
full-range reference is reported separately.

Outputs:
  e1_prefix/inputs/input_I1.md, input_I2.md
  e1_prefix/inputs/prefix_map.json
  e1_prefix/common_pool.json
  references/runner_reference_prefix_v1.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SUPPLEMENT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
PREFIX = SUPPLEMENT / "e1_prefix"
SOURCE_REPO = Path("E:/MaF-KG")

SOURCE = SOURCE_REPO / "data/raw/第四章_全章_无习题.md"
SOURCE_START = 5
RANGES = {
    "input_I1": {"source_start": 5, "source_end": 375, "label": "ch4_s1"},
    "input_I2": {"source_start": 5, "source_end": 947, "label": "ch4_s1_s2"},
}
I3 = {"input_I3": {"source_start": 5, "source_end": 1126, "label": "ch4_s1_s2_s3"}}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    raw = SOURCE.read_bytes()
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    source_sha = hashlib.sha256(raw).hexdigest().upper()

    ref = json.loads(
        (SUPPLEMENT / "references/reference_input_I_v2.json").read_text(encoding="utf-8"))

    (PREFIX / "inputs").mkdir(parents=True, exist_ok=True)
    exported = {}
    for sid, spec in RANGES.items():
        lo, hi = spec["source_start"], spec["source_end"]
        seg = lines[lo - 1:hi]
        out = PREFIX / "inputs" / f"{sid}.md"
        out.write_text("\n".join(seg) + "\n", encoding="utf-8", newline="\n")
        exported[sid] = {
            "input_id": sid,
            "source_file": str(SOURCE),
            "source_sha256": source_sha,
            "source_start_line": lo,
            "source_end_line": hi,
            "offset": lo - 1,
            "input_lines": len(seg),
            "input_chars_incl_lf": len("\n".join(seg)),
            "input_file": str(out.relative_to(WORKSPACE)).replace("\\", "/"),
            "input_sha256": hashlib.sha256(out.read_bytes()).hexdigest().upper(),
            "label": spec["label"],
        }
        assert len(seg) == hi - lo + 1

    # --- the frozen common pool -------------------------------------------
    # A reference item belongs to the common pool when its whole source evidence
    # lies inside I1 (L5-L375). Items whose evidence starts in I1 but continues
    # past it are excluded, because on the I1 range alone they are only partially
    # reachable and would make the denominator range-dependent.
    i1_hi = RANGES["input_I1"]["source_end"]
    common, excluded = [], []
    for n in ref["nodes"]:
        se = n.get("source_evidence") or n.get("evidence")
        row = {"id": n["id"], "type": n["type"], "name": n["name"], "status": n["status"],
               "tier": n.get("tier"), "section": n.get("section"),
               "source_evidence": se}
        if se and se[0] >= SOURCE_START and se[1] <= i1_hi:
            common.append(row)
        else:
            row["reason"] = "evidence extends past I1" if se and se[1] > i1_hi else "no evidence"
            excluded.append(row)

    # relations are treated the same way: both endpoints in the pool
    common_ids = {r["id"] for r in common}
    common_relations, excluded_relations = [], []
    for r in list(ref["relations"]) + list(ref.get("cross_section_relations", [])):
        se = r.get("source_evidence") or r.get("evidence")
        row = {"id": r["id"], "type": r["type"], "start": r["start"], "end": r["end"],
               "source_evidence": se}
        if se and se[0] >= SOURCE_START and se[1] <= i1_hi:
            common_relations.append(row)
        else:
            row["reason"] = "evidence extends past I1"
            excluded_relations.append(row)

    claims_in_pool = [r for r in common if r["type"] == "Claim"]
    pool = {
        "schema": "maf_kg.www_supplement.prefix_common_pool.v1",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 3.4",
        "frozen_before_predictions": True,
        "definition": "reference items of input I whose entire source evidence lies inside "
                      "I1 (source lines 5-375)",
        "i1_source_range": [SOURCE_START, i1_hi],
        "denominator_rule": "Every prefix range is scored against this one pool. The "
                            "denominator never changes with the range, so a trajectory is "
                            "comparable across I1, I2 and I3.",
        "items": common,
        "relations": common_relations,
        "item_count": len(common),
        "relation_count": len(common_relations),
        "required_item_count": sum(1 for r in common if r["status"] == "M"),
        "claim_count": len(claims_in_pool),
        "excluded_items": excluded,
        "excluded_relations": excluded_relations,
        "excluded_note": "Excluded because their evidence extends past I1, so they are not "
                         "fully reachable on the I1 range. They are still part of the "
                         "full-range reference and are reported there separately.",
    }

    # --- runner-facing reference for the prefix runs ----------------------
    # The frozen `reference_reachability` filters nodes by `node["section"] ==
    # section["id"]` and reads `node["evidence"]`, so the pool items must carry
    # the runner-facing section id and an `evidence` field. The original
    # input-internal section label is kept alongside as `input_section`.
    # The frozen reachability check groups nodes by `node["section"]`, so emit one
    # entry per (pool item, range). Both ranges contain the whole pool, so both
    # should report every item as fully reachable.
    runner_nodes = [
        {**r, "section": sid, "input_section": r.get("section"),
         "evidence": r.get("source_evidence"), "pool_id": r["id"]}
        for sid in ("input_I1", "input_I2") for r in common
    ]
    runner_relations = [
        {**r, "section": sid, "evidence": r.get("source_evidence"), "pool_id": r["id"]}
        for sid in ("input_I1", "input_I2") for r in common_relations
    ]
    sections = []
    for sid in ("input_I1", "input_I2"):
        e = exported[sid]
        sections.append({
            "id": sid, "source": e["source_file"], "source_sha256": e["source_sha256"],
            "start_line": e["source_start_line"], "end_line": e["source_end_line"],
            "label": e["label"],
        })
    runner_ref = {
        "version": "www-supplement.runner-reference.prefix.v1",
        "status": "FROZEN for E1p - built before any prediction",
        "date": "2026-10-11",
        "approval_basis": "assistant_source_reviewed; author approval pending",
        "note": "I3 (input_I) is not listed here: it reuses the E1 runs. Scoring uses "
                "common_pool.json for every range. The pool items carry "
                "section='input_I1' so the frozen reachability check finds them; their "
                "original input-internal section label is in input_section.",
        "planned_arms": ["direct", "maf"],
        "planned_method_runs": 8,
        "sections": sections,
        "nodes": runner_nodes,
        "relations": runner_relations,
        "relations_provisional": runner_relations,
        "must_not_merge": [], "boundary_groups": [], "uncertainties": [],
        "counts": {"items": len(runner_nodes), "relations": len(runner_relations)},
    }

    (PREFIX / "inputs/prefix_map.json").write_text(
        json.dumps({"schema": "maf_kg.www_supplement.prefix_map.v1",
                    "source_file": str(SOURCE), "source_sha256": source_sha,
                    "i3_note": "I3 is the E1 input input_I; its runs are reused, not rerun",
                    "ranges": {**exported, "input_I3": {
                        "source_start": 5, "source_end": 1126, "label": "ch4_s1_s2_s3",
                        "reuses_e1_runs": True}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (PREFIX / "common_pool.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (SUPPLEMENT / "references/runner_reference_prefix_v1.json").write_text(
        json.dumps(runner_ref, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    problems = []
    for sid, e in exported.items():
        if e["input_lines"] != e["source_end_line"] - e["source_start_line"] + 1:
            problems.append(f"{sid}: line count mismatch")
    if not common:
        problems.append("common pool is empty")
    if pool["required_item_count"] != len(common):
        problems.append("all pool items should be required (status M) in this reference")

    print(f"source      : {SOURCE.name} sha {source_sha[:16]}")
    for sid, e in exported.items():
        print(f"  {sid}: src L{e['source_start_line']}-{e['source_end_line']} "
              f"-> {e['input_lines']} lines, {e['input_chars_incl_lf']} chars")
    print(f"common pool : {pool['item_count']} items "
          f"({pool['required_item_count']} required, {pool['claim_count']} Claims) / "
          f"{pool['relation_count']} relations")
    print(f"excluded    : {len(excluded)} items, {len(excluded_relations)} relations")
    print(f"problems    : {len(problems)}")
    for p in problems:
        print(f"  - {p}")
    print(f"\nwrote {PREFIX.relative_to(WORKSPACE)}/common_pool.json")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
