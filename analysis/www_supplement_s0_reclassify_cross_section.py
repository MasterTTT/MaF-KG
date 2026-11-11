"""S0.5b correct the cross-section filing in input I's reference (v1 -> v2).

The builder filed nine relations under `cross_section_relations`, but four of
them (I-X1, I-X2, I-X3, I-X8) have both endpoints inside the same source
section; they cross a *subsection* boundary only. The frozen policy
(`review_merged/review_policy.json`) defines a cross-section relation as one
whose two endpoints belong to different source sections, and the earlier round
used that section-level convention. Leaving the filing as-is would make E1's
cross-section metric incomparable with the earlier round.

The relation set is not changed: the four rows move into `relations`, and the
finer subsection-level crossing is preserved in a separate informational field
so nothing is lost.

v1 is kept unchanged. No predictions exist yet, so this correction happens
before the reference is frozen for E1.

Outputs:
  references/reference_input_I_v2.json
  references/reference_input_I_v1_to_v2_diff.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
REFS = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11" / "references"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    v1_path = REFS / "reference_input_I_v1.json"
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))

    section_of = {n["id"]: n.get("section") for n in v1["nodes"]}
    name_of = {n["id"]: n.get("name") for n in v1["nodes"]}

    kept, moved = [], []
    for r in v1.get("cross_section_relations", []):
        s, e = section_of.get(r.get("start")), section_of.get(r.get("end"))
        if s and e and s != e:
            kept.append(r)
        else:
            moved.append({**r, "section": s,
                          "reclassified_because": "both endpoints share one source section; "
                                                  "the relation crosses a subsection boundary only"})

    v2 = dict(v1)
    v2["version"] = "input-I.source-reference.v2"
    v2["status"] = ("DRAFT - assistant source review, author approval pending; v2 corrects the "
                    "cross-section filing of v1")
    v2["relations"] = list(v1.get("relations", [])) + moved
    v2["cross_section_relations"] = kept
    v2["subsection_crossing_relations"] = [
        {"id": m["id"], "type": m["type"], "start": m["start"], "end": m["end"],
         "section": m["section"],
         "start_name": name_of.get(m["start"]), "end_name": name_of.get(m["end"])}
        for m in moved
    ]
    counts = dict(v1.get("counts", {}))
    for key in ("relations_intra", "relations"):
        if key in counts:
            counts[key] = len(v2["relations"])
    for key in ("relations_cross", "cross_section_relations"):
        if key in counts:
            counts[key] = len(kept)
    counts["subsection_crossing_relations"] = len(moved)
    v2["counts"] = counts
    v2["revision_note"] = (
        "v2 moves four relations out of cross_section_relations because their endpoints share one "
        "source section. The frozen policy and the earlier round both use the section-level "
        "convention. No relation was added, removed or relabelled; the subsection-level crossing is "
        "recorded in subsection_crossing_relations.")

    diff = {
        "schema": "maf_kg.www_supplement.reference_diff.v1",
        "reference": "input I",
        "from": "reference_input_I_v1.json",
        "to": "reference_input_I_v2.json",
        "reason": "cross-section filing must follow the frozen section-level convention so that "
                  "the E1 cross-section metric is comparable with the earlier round",
        "before": {"relations": len(v1.get("relations", [])),
                   "cross_section_relations": len(v1.get("cross_section_relations", []))},
        "after": {"relations": len(v2["relations"]),
                  "cross_section_relations": len(v2["cross_section_relations"]),
                  "subsection_crossing_relations": len(v2["subsection_crossing_relations"])},
        "moved": [{"id": m["id"], "type": m["type"], "start": m["start"], "end": m["end"],
                   "start_section": section_of.get(m["start"]),
                   "end_section": section_of.get(m["end"]),
                   "start_name": name_of.get(m["start"]),
                   "end_name": name_of.get(m["end"])} for m in moved],
        "kept_cross_section": [{"id": r["id"], "type": r["type"],
                                "start_section": section_of.get(r["start"]),
                                "end_section": section_of.get(r["end"])} for r in kept],
        "unchanged": ["every node", "every relation's meaning and endpoints",
                      "must_not_merge", "boundary_groups", "uncertainties",
                      "signature_exceptions"],
        "predictions_existing": 0,
    }

    (REFS / "reference_input_I_v2.json").write_text(
        json.dumps(v2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REFS / "reference_input_I_v1_to_v2_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"v1: relations={diff['before']['relations']} cross={diff['before']['cross_section_relations']}")
    print(f"v2: relations={diff['after']['relations']} cross={diff['after']['cross_section_relations']} "
          f"subsection_crossing={diff['after']['subsection_crossing_relations']}")
    for m in diff["moved"]:
        print(f"  moved {m['id']:6s} {m['start_section']}->{m['end_section']}  "
              f"{m['start_name'][:24]} -> {m['end_name'][:24]}")
    print(f"kept cross-section: {[r['id'] for r in diff['kept_cross_section']]}")
    print(f"\nwrote {(REFS / 'reference_input_I_v2.json').relative_to(WORKSPACE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
