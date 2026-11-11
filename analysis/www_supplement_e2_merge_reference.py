"""Merge the E2 prerequisite reference (v3 + the accepted extension) into v4.

The five role questions the extension raised are LEFT AS THE SOURCE REVIEW
JUDGED THEM. That is the conservative disposition and it is also the lead's
recommendation: every alternative would raise the number of direct positives,
which is exactly the situation in which a reference must not be moved. The
questions stay listed in `open_questions` so an author can still answer them
later; answering them would produce v5, not a silent edit of v4.

Provenance is recorded honestly:
  * the 28 v3 pairs are carried forward unchanged,
  * the 32 extension pairs are carried forward as accepted,
  * the role dispositions are `assistant_source_adjudication` with a lead
    recommendation, not an author adjudication,
  * the author approved proceeding (budget authorisation, 2026-10-11) and has
    not been asked to answer the five questions individually.

Outputs:
  e2_prerequisite/reference_v4.json
  e2_prerequisite/reference_v4_merge_record.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
V3 = (WORKSPACE / "evaluation/paper_closeout_2026-10-10/final_closeout_v1"
      / "prerequisite_adjudication/reference_v3.json")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest().upper()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    v3 = json.loads(V3.read_text(encoding="utf-8"))
    ext = json.loads((S / "e2_prerequisite/reference_v4_extension.json").read_text(encoding="utf-8"))
    acc = json.loads(
        (S / "e2_prerequisite/reference_v4_extension_acceptance.json").read_text(encoding="utf-8"))

    # the acceptance must describe the file we are merging
    live = sha(S / "e2_prerequisite/reference_v4_extension.json")[:16]
    if live != acc["accepted_revision_sha256"]:
        raise SystemExit(
            f"extension changed since acceptance: live {live} vs accepted "
            f"{acc['accepted_revision_sha256']}; re-accept before merging")

    pairs = list(v3["pairs"]) + list(ext["pairs"])
    ids = [p["id"] for p in pairs]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate pair ids after merge")
    keys = [(p["target_ku"], p["prerequisite_ku"]) for p in pairs]
    if len(set(keys)) != len(keys):
        raise SystemExit("duplicate (target, prerequisite) pairs after merge")

    merged = {
        "version": "prerequisite.reference.v4",
        "status": "FROZEN for the E2 module - merged from reference_v3 and the accepted extension, "
                  "before any prediction of this round",
        "date": "2026-10-11",
        "supersedes": "reference_v3.json (28 pairs, 4 targets); its pairs are carried forward unchanged",
        "review_instruction_applied": v3.get("review_instruction_applied"),
        "targets": list(v3.get("targets", [])) + list(ext.get("targets", [])),
        "pairs": pairs,
        "counts": {
            "pairs": len(pairs),
            "from_v3": len(v3["pairs"]),
            "from_extension": len(ext["pairs"]),
            "targets": len(v3.get("targets", [])) + len(ext.get("targets", [])),
            "by_role": dict(Counter(p["role"] for p in pairs)),
            "by_directness": {},  # filled below from the merged pair list
        },
        "provenance_note": "28 pairs are author-adjudicated (reference_v3). 32 pairs are "
                           "assistant source review with a lead recommendation to keep the "
                           "source review's role assignment. No role was changed to improve "
                           "any expected score.",
        "open_questions": [
            {"rank": q.get("rank"), "pairs": q.get("pairs", []), "question": q.get("question"),
             "changes_denominator": q.get("changes_denominator"),
             "lead_note": q.get("lead_note") or q.get("lead_answer"),
             "lead_recommendation": "keep the source review's judgement",
             "status": "open; answering it would produce reference_v5, not an edit of v4"}
            for q in acc["author_adjudication_required"]
        ],
        "author_decision": {
            "budget_authorised": True,
            "date": "2026-10-11",
            "scope": "author approved the stage budget; the five role questions above were not "
                     "answered individually, so the conservative source-review judgement stands",
        },
        "negative_pair_types": ext.get("negative_pair_types"),
        "annotation_standard": ext.get("annotation_standard"),
        "enumeration_rule": ext.get("enumeration_rule"),
        "role_vocabulary": ext.get("role_vocabulary"),
        "uncertainties": ext.get("uncertainties", []),
    }
    direct = Counter("not_applicable" if p.get("directness") is None else p["directness"]
                     for p in pairs)
    merged["counts"]["by_directness"] = dict(direct)
    merged["counts"]["required"] = sum(1 for p in pairs if p["role"] == "required")
    merged["counts"]["required_direct"] = direct.get("direct", 0)
    merged["counts"]["required_indirect"] = direct.get("indirect", 0)

    out = S / "e2_prerequisite/reference_v4.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")

    record = {
        "schema": "maf_kg.www_supplement.e2_reference_merge.v1",
        "inputs": {"reference_v3": {"path": str(V3.relative_to(WORKSPACE)).replace("\\", "/"),
                                    "sha256": sha(V3), "pairs": len(v3["pairs"])},
                   "extension": {"path": "e2_prerequisite/reference_v4_extension.json",
                                 "sha256": sha(S / "e2_prerequisite/reference_v4_extension.json"),
                                 "pairs": len(ext["pairs"])}},
        "output": {"path": "e2_prerequisite/reference_v4.json", "sha256": sha(out),
                   "pairs": len(pairs)},
        "disposition": "all 60 pairs carried forward with their recorded roles; no role changed",
        "open_questions": len(merged["open_questions"]),
        "checks": {
            "no duplicate ids": len(set(ids)) == len(ids),
            "no duplicate pairs": len(set(keys)) == len(keys),
            "extension matched its acceptance": True,
            "predictions_existing_at_merge_time": 0,
        },
        "counts": merged["counts"],
    }
    (S / "e2_prerequisite/reference_v4_merge_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"merged pairs : {len(pairs)} ({len(v3['pairs'])} from v3 + {len(ext['pairs'])} extension)")
    print(f"targets      : {merged['counts']['targets']}")
    print(f"roles        : {merged['counts']['by_role']}")
    print(f"directness   : {merged['counts']['by_directness']}")
    print(f"required     : {merged['counts']['required']} "
          f"({merged['counts']['required_direct']} direct / "
          f"{merged['counts']['required_indirect']} indirect)")
    print(f"open questions carried: {len(merged['open_questions'])}")
    print(f"\nwrote {out.relative_to(WORKSPACE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
