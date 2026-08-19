#!/usr/bin/env python
"""Check a KnowledgeUnit JSON against the KU Gold (evaluation/gold/ku_gold_ch1_v1.json).

For every gold unit (matched by ku_id, else by name):

  * FAIL  - a gold expected center is missing from the unit's centers;
  * WARN  - the unit has a center not listed in the gold (a defensive center
            choice is acceptable, but each occurrence should be reviewed);
  * FAIL  - a gold must-contain node is absent from centers+contains;
  * FAIL  - a forbidden merge pair: one of the two units is missing entirely
            (would mean the two objectives were merged into one unit).

Static gold findings (归属漂移/L1 重复抽取) are printed for visibility and also
emitted as a `findings` section of the report for later action tracking.

Usage:
  python scripts/check_ku_gold.py <ku.json> [--gold evaluation/gold/ku_gold_ch1_v1.json]
      [--report reports/ku_gold_check_ch1_v1.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Check KUs against the KU Gold.")
    ap.add_argument("ku", help="KnowledgeUnit JSON (e.g. 第一章_knowledge_units_v6.json).")
    ap.add_argument("--gold", default="evaluation/gold/ku_gold_ch1_v1.json")
    ap.add_argument("--report", help="Optional JSON report path.")
    args = ap.parse_args()

    ku = json.loads(Path(args.ku).read_text(encoding="utf-8"))
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    units = ku["knowledge_units"]
    by_id = {u["id"]: u for u in units}
    by_name = {u["name"]: u for u in units}

    def member_names(u: dict) -> set[str]:
        names = set()
        for m in u.get("centers_on", []) + u.get("contains", []):
            names.add(m["name"])
        return names

    issues: list[dict] = []
    for g in gold["units"]:
        u = by_id.get(g["ku_id"]) or by_name.get(g["name"])
        if u is None:
            issues.append({"kind": "FAIL", "unit": g["name"],
                           "detail": f"Gold 单元 {g['name']} 在 KU 中不存在（可能被合并/改名）"})
            continue
        center_names = {m["name"] for m in u.get("centers_on", [])}
        all_names = center_names | member_names(u)
        for c in g.get("centers", []):
            if c not in center_names:
                issues.append({"kind": "FAIL", "unit": u["name"],
                               "detail": f"期望中心缺失: {c}（当前 centers: {'、'.join(sorted(center_names)) or '空'}）"})
        for extra in sorted(center_names - set(g.get("centers", []))):
            issues.append({"kind": "WARN", "unit": u["name"], "detail": f"额外中心: {extra}（可辩护，需人工确认）"})
        for m in g.get("contains_must", []):
            if m not in all_names:
                issues.append({"kind": "FAIL", "unit": u["name"], "detail": f"必含节点缺失: {m}"})

    for pair in gold.get("forbidden_merge_pairs", []):
        a, b = pair["ku_a"], pair["ku_b"]
        if a not in by_name:
            issues.append({"kind": "FAIL", "unit": a,
                           "detail": f"禁止合并对 {a}/{b}：{a} 缺失（可能被并入 {b}）—— {pair['reason']}"})
        if b not in by_name:
            issues.append({"kind": "FAIL", "unit": b,
                           "detail": f"禁止合并对 {a}/{b}：{b} 缺失（可能被并入 {a}）—— {pair['reason']}"})

    fails = [i for i in issues if i["kind"] == "FAIL"]
    warns = [i for i in issues if i["kind"] == "WARN"]
    print(f"KU: {len(units)}  检查项 FAIL {len(fails)} / WARN {len(warns)}")
    for i in sorted(issues, key=lambda x: (x["kind"] != "FAIL", x["unit"])):
        print(f"  [{i['kind']}] {i['unit']}: {i['detail']}")

    print("\n--- Gold findings（静态，供后续修复追踪）---")
    for f in gold.get("findings", []):
        print(f"  {f['id']} [{f['severity']}] {f['kind']}: {f['detail'][:90]}")

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps({
            "schema_version": "maf-kg.knowledge-unit.gold.check.v1",
            "gold": args.gold,
            "ku": args.ku,
            "units_checked": len(gold["units"]),
            "fail_count": len(fails),
            "warn_count": len(warns),
            "issues": issues,
            "findings": gold.get("findings", []),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote report -> {args.report}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())