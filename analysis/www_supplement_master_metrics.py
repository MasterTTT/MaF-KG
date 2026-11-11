"""Master metrics table across the four experiments.

Reads the per-experiment metric files and emits one table plus a JSON record.
It does not compute anything new; it only joins what the experiment-specific
scorers already produced, so a number can never be created twice under two
different rules.

Design rules carried into the output:
  * content, conditions, relations and cost are separate universes; no pooled F1
  * the strict relation column always carries its comparable denominator
  * E2's P+S numbers come from the version that actually executed (v3), and the
    two failed attempts are reported alongside, never dropped
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"


def read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def pct(d):
    if not d or d.get("value") is None:
        return "n/a"
    return f"{d['numerator']}/{d['denominator']} ({d['value']:.3f})"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out: dict = {"schema": "maf_kg.www_supplement.master_metrics.v1", "date": "2026-10-11"}

    # ---- E1 -------------------------------------------------------------
    p = S / "e1_content/e1_metrics.json"
    if p.exists():
        e1 = read(p)
        out["E1"] = {
            "question": "does sequential construction with budgeted graph memory preserve more "
                        "complete mathematical content than single-pass extraction, on two new "
                        "consecutive inputs",
            "denominator_rule": e1["denominator_rule"],
            "conditions_policy": e1["conditions_policy"],
            "correction_applied": e1.get("correction_applied"),
            "per_method": e1["per_method"],
            "caveat": "iText2KG's native entities carry only a name, a label and an embedding, "
                      "never a description; under the contract's carrier rule the substantive "
                      "meaning of an item cannot be credited from a bare name, so its content "
                      "column measures what that representation can carry rather than how much "
                      "mathematics it located",
        }
    # ---- E1p ------------------------------------------------------------
    p = S / "reports/e1_e3_metrics.json"
    if p.exists():
        d = read(p)
        if "E1p" in d:
            out["E1p"] = {
                "question": "on one frozen early-content pool, how does retention change as the "
                            "input is extended",
                "pool": "9 required items and 9 relations from the first section only; the "
                        "denominator never changes across ranges",
                "pooled": d["E1p"]["pooled"],
                "per_run": {k: v["complete"] for k, v in d["E1p"]["per_run"].items()},
                "caveat": "the content column is at ceiling: the nine pool items are extracted "
                          "well by every run, so the discriminating signal is in the relation "
                          "columns",
            }
        if "E3" in d:
            out["E3"] = {
                "question": "what does model-visible memory contribute to content, conditions, "
                            "identity and connectivity at the primary configuration",
                "arm_reported_here": "no visible memory (4 runs)",
                "pooled": d["E3"]["pooled"],
                "per_run": {k: v["complete"] for k, v in d["E3"]["per_run"].items()},
                "caveat": "the memory arm is the E1 MaF run, so the contrast must be read across "
                          "E1 and E3, not inside either one",
            }
    # ---- E2 -------------------------------------------------------------
    p = S / "e2_prerequisite/e2_scores.json"
    if p.exists():
        d = read(p)
        by = d.get("by_arm", {})
        out["E2"] = {
            "question": "does semantic candidate completion add real end-to-end prerequisite "
                        "gain, or new errors",
            "version_reported": "v3 (output ceiling 32000, a recorded parameter deviation)",
            "by_arm": {
                a: {
                    "candidate_direct_recall_merged": v.get("candidate_direct_recall", {}).get("merged"),
                    "end_to_end_direct": v.get("end_to_end_direct"),
                    "verdict_coverage": v.get("verdict_coverage"),
                } for a, v in by.items()
            },
            "failed_attempts": ["v1 at the plan's 12000 ceiling: P+S failed twice because "
                                "reasoning consumed the whole output budget",
                                "v2: 1 of 4 runs, caused by killing worker processes and leaving "
                                "unsettled ledger entries"],
            "out_of_reference": "0 accepted direct edges outside the reference; the separate "
                                "source-text review required by the plan therefore has no object "
                                "for the direct-edge table",
        }
    # ---- cost -----------------------------------------------------------
    total = 0.0
    ledgers = 0
    for p in S.rglob("*.json"):
        if p.name == "budget.json" or p.parent.name in ("e1_ledgers_a2", "e1p_ledgers",
                                                        "e3_ledgers"):
            try:
                v = read(p).get("accounting_usd") or 0
            except Exception:
                continue
            total += v
            ledgers += 1
    out["cost"] = {"ledgers_counted": ledgers, "total_usd": round(total, 4),
                   "includes": ["the completed runs", "E2 v1 and v2 failed attempts",
                                "the E1p contract-violation failure"]}

    (S / "reports/master_metrics.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print("== MASTER ==")
    if "E1" in out:
        print("E1 (two new inputs, 2 repeats each):")
        for m, v in out["E1"]["per_method"].items():
            print(f"   {m:10s} complete {pct(v['complete_required']):>18s}  "
                  f"recognized {pct(v['recognized_required']):>18s}  "
                  f"conditions {pct(v['condition_retention']):>14s}")
    if "E1p" in out:
        print(f"E1p pooled: complete {pct(out['E1p']['pooled']['complete_required'])}  "
              f"conditions {pct(out['E1p']['pooled']['condition_retention'])}")
    if "E3" in out:
        print(f"E3 (no memory) pooled: complete {pct(out['E3']['pooled']['complete_required'])}  "
              f"conditions {pct(out['E3']['pooled']['condition_retention'])}")
    if "E2" in out:
        for a, v in out["E2"]["by_arm"].items():
            f1 = (v["end_to_end_direct"] or {}).get("f1") or {}
            print(f"E2 arm {a:5s}: end-to-end direct F1 mean "
                  f"{f1.get('mean') if f1.get('mean') is None else round(f1['mean'], 4)}")
    print(f"cost: ${out['cost']['total_usd']} over {out['cost']['ledgers_counted']} ledgers")
    print(f"\nwrote reports/master_metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
