#!/usr/bin/env python
"""Keep only depends_on candidates not yet decided in an existing review.

Used for the cross-chapter extension: the Chapter-1 review (v4) settled every
intra-chapter-1 candidate pair. When Chapter-2 KUs are added, candidate
generation produces pairs that are already decided (v4 accepted or v4
rejected) plus genuinely new pairs (intra-chapter-2 and cross-chapter). This
script filters the candidate JSON down to the new pairs so the LLM review
only re-judges what is new.

Usage:
  python scripts/filter_undecided_candidates.py <candidates.json> \
      --v4 <depends_v4.json> --rejected-from <review_v2.json> --out <subset.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Filter candidates to undecided pairs.")
    ap.add_argument("candidates", help="Candidate JSON (build_ku_depends_candidates.py output).")
    ap.add_argument("--v4", required=True, help="Existing depends JSON (accepted pairs).")
    ap.add_argument("--rejected-from", help="Review JSON whose rejected verdicts define the decided-reject set.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cand = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    v4 = json.loads(Path(args.v4).read_text(encoding="utf-8"))
    decided = {(e["source_ku"], e["target_ku"]) for e in v4.get("depends_on", [])}
    if args.rejected_from:
        rev = json.loads(Path(args.rejected_from).read_text(encoding="utf-8"))
        for v in rev.get("verdicts", []):
            if not v["accept"]:
                decided.add((v["source_ku"], v["target_ku"]))

    kept = [c for c in cand["candidates"] if (c["source_ku"], c["target_ku"]) not in decided]
    out = dict(cand)
    out["candidates"] = kept
    out["diagnostics"] = {
        "total_candidates": len(cand["candidates"]),
        "kept_undecided": len(kept),
        "dropped_decided": len(cand["candidates"]) - len(kept),
        "note": "subset of candidates whose pair was not decided in v4/rejected set",
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"candidates {len(cand['candidates'])} -> kept {len(kept)} (dropped {len(cand['candidates']) - len(kept)})")
    for c in kept:
        print(f"  {c['source_name']} -> {c['target_name']}  ({'、'.join(s['type'] for s in c.get('signals', [])) or 'chain'})")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())