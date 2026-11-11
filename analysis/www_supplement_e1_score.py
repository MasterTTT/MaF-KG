"""E1 layered scorer: turn review judgements into the contract's metrics.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 3.5.
Contract: evaluation/www_evidence_supplement_2026-10-11/freeze/evaluation_contract.json

The scorer is deliberately narrow. It does not decide anything about mathematics;
it only aggregates judgements that a review pass already made, under the frozen
rules. Two rules are easy to get wrong and are enforced here:

  * A required Claim the method never delivered counts as conditions NOT
    retained. The condition metric is not scored only over the Claims a method
    happened to find.
  * The strict relation column prints BOTH the full-relation denominator and the
    comparable denominator, so the two can never be silently mixed.

Inputs
  --reference   a runner-facing reference (nodes + relations with status/type)
  --judgements  one or more JSON files, each a list of rows:
                  {"run": "...", "item_id": "...", "coverage": "...",
                   "conditions_complete": true|false, "source_support": "..."}
                and/or relation rows:
                  {"run": "...", "relation_id": "...", "semantic": "...",
                   "strict": "...", "native_signature": "..."}
  --events      optional per-run duplicate / false-merge counts

Output: metrics JSON plus a Markdown table.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

COVERAGE = ("full", "partial", "name_only", "missing", "not_applicable")
SEMANTIC = ("full", "partial", "mentioned_only", "not_covered", "uncertain")
STRICT = ("conforms", "direction_mismatch", "type_mismatch",
          "endpoint_identity_mismatch", "not_comparable", "uncertain", "absent")


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def split_rows(payload) -> tuple[list, list]:
    """Accept either a flat list or a dict with item_rows / relation_rows."""
    if isinstance(payload, list):
        items = [r for r in payload if "item_id" in r]
        rels = [r for r in payload if "relation_id" in r]
        return items, rels
    return payload.get("item_rows", []), payload.get("relation_rows", [])


def score(reference: dict, item_rows: list, relation_rows: list,
          events: dict | None = None) -> dict:
    nodes = reference["nodes"]
    relations = reference.get("relations", []) + reference.get("cross_section_relations", [])
    required = [n for n in nodes if n.get("status") == "M"]
    core = [n for n in required if n.get("tier") == "core"]
    detail = [n for n in required if n.get("tier") == "detail"]
    required_claims = [n for n in required if n.get("type") == "Claim"]

    runs = sorted({r["run"] for r in item_rows} | {r["run"] for r in relation_rows})
    by_run_item = {(r["run"], r["item_id"]): r for r in item_rows}
    by_run_rel = {(r["run"], r["relation_id"]): r for r in relation_rows}

    out: dict = {"schema": "maf_kg.www_supplement.e1_metrics.v1",
                 "reference": {"nodes": len(nodes), "required": len(required),
                               "core": len(core), "detail": len(detail),
                               "required_claims": len(required_claims),
                               "relations": len(relations)},
                 "runs": {}}

    for run in runs:
        row: dict = {}

        # ---- content ----------------------------------------------------
        def cov(items, kinds=("full",)):
            hit = sum(1 for n in items
                      if (r := by_run_item.get((run, n["id"]))) and r.get("coverage") in kinds)
            return {"numerator": hit, "denominator": len(items),
                    "value": round(hit / len(items), 6) if items else None}

        row["complete_required"] = cov(required, ("full",))
        row["recognized_required"] = cov(required, ("full", "partial", "name_only"))
        row["complete_core"] = cov(core, ("full",))
        row["complete_detail"] = cov(detail, ("full",))
        row["recognized_core"] = cov(core, ("full", "partial", "name_only"))
        row["recognized_detail"] = cov(detail, ("full", "partial", "name_only"))

        # ---- conditions -------------------------------------------------
        # A required Claim that is missing counts as NOT retained, so the
        # denominator is every required Claim, not only the delivered ones.
        retained, missing_claim, no_condition, unjudged_condition = 0, 0, 0, 0
        per_claim = []
        for n in required_claims:
            r = by_run_item.get((run, n["id"]))
            cov_v = (r or {}).get("coverage")
            cc = (r or {}).get("conditions_complete")
            if cc is not None and type(cc) is not bool:
                raise ValueError("conditions_complete must be a boolean or null")
            if cov_v == "missing" or r is None:
                missing_claim += 1
            if cc is True and cov_v in ("full", "partial", "name_only"):
                retained += 1
            elif cc is False and cov_v in ("full", "partial", "name_only"):
                no_condition += 1
            elif cov_v in ("full", "partial", "name_only"):
                unjudged_condition += 1
            per_claim.append({"item_id": n["id"], "name": n.get("name"),
                              "coverage": cov_v, "conditions_complete": cc})
        row["condition_retention"] = {
            "numerator": retained, "denominator": len(required_claims),
            "value": (round(retained / len(required_claims), 6)
                      if required_claims and not unjudged_condition else None),
            "lower_bound": round(retained / len(required_claims), 6) if required_claims else None,
            "upper_bound": (round((retained + unjudged_condition) / len(required_claims), 6)
                            if required_claims else None),
            "complete": not unjudged_condition,
            "claims_with_unjudged_conditions": unjudged_condition,
            "claims_not_delivered": missing_claim,
            "claims_delivered_without_complete_conditions": no_condition,
            "rule": "a required Claim that was not delivered counts as conditions not retained",
        }
        row["conditions_per_claim"] = per_claim

        # ---- relations --------------------------------------------------
        sem = defaultdict(int)
        for r in relations:
            j = by_run_rel.get((run, r["id"]))
            sem[(j or {}).get("semantic", "unjudged")] += 1
        strict_counts = defaultdict(int)
        for r in relations:
            j = by_run_rel.get((run, r["id"]))
            strict_counts[(j or {}).get("strict", "unjudged")] += 1
        total_rel = len(relations)
        comparable = total_rel - strict_counts.get("not_comparable", 0) \
            - strict_counts.get("absent", 0) - strict_counts.get("unjudged", 0)
        row["relation_semantic"] = {
            "counts": dict(sem),
            "full": sem.get("full", 0),
            "partial": sem.get("partial", 0),
            "full_plus_partial": sem.get("full", 0) + sem.get("partial", 0),
            "denominator": total_rel,
            "note": "full and partial are reported separately; their sum is never called correctness",
        }
        row["relation_strict"] = {
            "counts": dict(strict_counts),
            "conforms_over_all_relations": {
                "numerator": strict_counts.get("conforms", 0), "denominator": total_rel,
                "value": round(strict_counts.get("conforms", 0) / total_rel, 6) if total_rel else None},
            "conforms_over_comparable": {
                "numerator": strict_counts.get("conforms", 0), "denominator": comparable,
                "value": round(strict_counts.get("conforms", 0) / comparable, 6) if comparable else None},
            "comparable_denominator": comparable,
            "rule": "both denominators are printed; the comparable denominator excludes "
                    "not_comparable, absent and unjudged rows",
        }
        native = defaultdict(int)
        for r in relations:
            j = by_run_rel.get((run, r["id"]))
            if j and "native_signature" in j:
                native[j["native_signature"]] += 1
        row["native_signature_validity"] = {
            "counts": dict(native), "judged": sum(native.values()),
            "note": "separate column; reference-edge agreement is not renamed schema validity",
        }

        ev = (events or {}).get(run, {})
        row["output_events"] = {"duplicate_repetitions": ev.get("duplicate_repetitions"),
                                "duplicate_splits": ev.get("duplicate_splits"),
                                "false_merge_events": ev.get("false_merge_events")}
        row["cost"] = ev.get("cost", {})
        row["completion"] = ev.get("completion")
        out["runs"][run] = row

    return out


def verdict_coverage(determinate: int, judged_and_determinate: int) -> dict:
    """The plan's rule: |judged and determinate| / |determinate|.

    Never one minus the abstention rate, which would silently credit pairs that
    were never judged at all.
    """
    return {"numerator": judged_and_determinate, "denominator": determinate,
            "value": round(judged_and_determinate / determinate, 6) if determinate else None,
            "rule": "|judged and determinate| / |determinate|"}


def markdown(metrics: dict) -> str:
    lines = ["# E1 分层评分（按运行）", "",
             f"参考规模：{metrics['reference']}", "",
             "| 运行 | 完整必抽 | 识别必抽 | core 完整 | detail 完整 | 条件保留 | 关系 full | 关系 partial | 严格 conforms（可比分母） |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run, r in metrics["runs"].items():
        def f(d):
            return "n/a" if d["value"] is None else f"{d['numerator']}/{d['denominator']} ({d['value']:.3f})"
        lines.append(
            f"| {run} | {f(r['complete_required'])} | {f(r['recognized_required'])} | "
            f"{f(r['complete_core'])} | {f(r['complete_detail'])} | "
            f"{f(r['condition_retention'])} | "
            f"{r['relation_semantic']['full']}/{r['relation_semantic']['denominator']} | "
            f"{r['relation_semantic']['partial']}/{r['relation_semantic']['denominator']} | "
            f"{f(r['relation_strict']['conforms_over_comparable'])} |")
    lines += ["",
              "条件保留的分母是全部必需 Claim；**未交付的 Claim 计为条件未保留**。",
              "关系 full 与 partial 分列，其和不称为正确率。",
              "严格列同时给出全量分母与可比分母，不混用。",
              ""]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--judgements", type=Path, nargs="+", required=True)
    ap.add_argument("--events", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    reference = read(args.reference)
    items, rels = [], []
    for p in args.judgements:
        i, r = split_rows(read(p))
        items += i
        rels += r
    events = read(args.events) if args.events else {}
    metrics = score(reference, items, rels, events)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    md = args.out.with_suffix(".md")
    md.write_text(markdown(metrics), encoding="utf-8", newline="\n")
    print(markdown(metrics))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
