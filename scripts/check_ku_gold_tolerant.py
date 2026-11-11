#!/usr/bin/env python
"""Tolerant KU Gold check for section-granularity pipelines (8/27 new pipeline).

Maps each Gold objective onto target units by member-name overlap (exact match
= 2 分; 4 字以上双向子串 = 1 分) instead of requiring exact unit names:

  * FAIL - 目标丢失: 没有任何目标单元承载其期望中心/必含节点;
  * FAIL - 禁合并对: 两个目标落入同一目标单元;
  * FAIL - 期望中心缺失: 期望中心节点在单元 centers 与 contains 中都不存在;
  * FAIL - 必含节点缺失: 必含节点在单元 centers 与 contains 中都不存在;
  * FAIL - 零中心单元: centers 为空但 contains 非空（schema 要求 >=1 center）;
  * WARN - 期望中心未设为中心: 节点存在于 contains 但未设为 center;
  * WARN - 复合单元: 一个目标单元吸并 >2 个 Gold 目标;
  * WARN - 空壳单元: centers 非空但 contains 为空;
  * WARN - 超限中心: centers_on 数量 >3（schema 上限）。

Usage:
  python scripts/check_ku_gold_tolerant.py <ku.json>
      [--gold evaluation/gold/ku_gold_ch1_v2.json] [--report <path>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def mem_names(u: dict) -> set[str]:
    out: set[str] = set()
    for m in u.get('centers_on', []) + u.get('contains', []):
        if isinstance(m, dict):
            if m.get('name'):
                out.add(m['name'])
        elif isinstance(m, str):
            out.add(m)
    return out


def center_names(u: dict) -> set[str]:
    return {m['name'] for m in u.get('centers_on', []) if isinstance(m, dict) and m.get('name')}


def match_score(gold_name: str, pool: set[str]) -> int:
    if gold_name in pool:
        return 2
    if len(gold_name) >= 4 and any(gold_name in n or n in gold_name for n in pool if len(n) >= 4):
        return 1
    return 0


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Tolerant KU Gold check.')
    ap.add_argument('ku')
    ap.add_argument('--gold', default='evaluation/gold/ku_gold_ch1_v2.json')
    ap.add_argument('--report')
    args = ap.parse_args()

    ku = json.loads(Path(args.ku).read_text(encoding='utf-8'))
    gold = json.loads(Path(args.gold).read_text(encoding='utf-8'))
    units = ku['knowledge_units']

    pools = {u['name']: mem_names(u) for u in units}
    ctrs = {u['name']: center_names(u) for u in units}
    unames = list(pools)

    # map each gold objective -> scored target units
    gold_targets: dict[str, dict[str, int]] = {}
    for g in gold['units']:
        expected = list(g.get('centers', [])) + list(g.get('contains_must', []))
        scored: dict[str, int] = {}
        for un in unames:
            s = sum(match_score(e, pools[un]) for e in expected)
            if s:
                scored[un] = s
        gold_targets[g['name']] = scored

    issues: list[dict] = []
    # 强匹配（得分>=2）的吸纳关系，用于复合单元度量
    absorbed: dict[str, set[str]] = {un: set() for un in unames}

    for g in gold['units']:
        tgt = gold_targets[g['name']]
        if not tgt:
            issues.append({'kind': 'FAIL', 'unit': g['name'],
                           'detail': '目标丢失：没有目标单元承载其任一期望中心/必含节点'})
            continue
        for un, s in tgt.items():
            if s >= 2:
                absorbed[un].add(g['name'])
        best = max(sorted(tgt.items(), key=lambda x: (x[1], x[0])), key=lambda x: x[1])[0]
        ctr = ctrs[best]
        pool = pools[best]
        for c in g.get('centers', []):
            if match_score(c, ctr) == 0:
                if match_score(c, pool) > 0:
                    issues.append({'kind': 'WARN', 'unit': g['name'],
                                   'detail': f'期望中心未设为中心: {c}（在 {best} 的 contains 中，未设为 center）'})
                else:
                    issues.append({'kind': 'FAIL', 'unit': g['name'],
                                   'detail': f'期望中心缺失: {c}（最佳承载 {best}，centers+contains 均无此节点；当前中心: {"、".join(sorted(ctr)) or "空"}）'})
        for m in g.get('contains_must', []):
            if match_score(m, pool) == 0:
                issues.append({'kind': 'FAIL', 'unit': g['name'],
                               'detail': f'必含节点缺失: {m}（最佳承载 {best}，centers+contains 均无此节点）'})

    for p in gold.get('forbidden_merge_pairs', []):
        a, b = p['ku_a'], p['ku_b']
        shared = set(gold_targets.get(a, {})) & set(gold_targets.get(b, {}))
        if shared:
            issues.append({'kind': 'FAIL', 'unit': f'{a}/{b}',
                           'detail': f'禁合并对被违反：{a} 与 {b} 同落于 {"、".join(sorted(shared))}—— {p["reason"]}'})

    for un in unames:
        if len(ctrs[un]) == 0 and pools[un]:
            issues.append({'kind': 'FAIL', 'unit': un,
                           'detail': '零中心单元: centers_on 为空但 contains 非空（schema 要求 >=1 center）'})
        if len(ctrs[un]) > 3:
            issues.append({'kind': 'WARN', 'unit': un,
                           'detail': f'超限中心: {len(ctrs[un])} 个（schema centers_on <= 3）'})
        if not pools[un] and ctrs[un]:
            issues.append({'kind': 'WARN', 'unit': un,
                           'detail': '空壳单元: contains 为空（仅剩中心）'})

    fails = [i for i in issues if i['kind'] == 'FAIL']
    warns = [i for i in issues if i['kind'] == 'WARN']
    print(f'KU: {len(units)}  Gold 目标: {len(gold["units"])}  检查项 FAIL {len(fails)} / WARN {len(warns)}')
    for i in sorted(issues, key=lambda x: (x['kind'] != 'FAIL', x['unit'])):
        print(f"  [{i['kind']}] {i['unit']}: {i['detail']}")

    print('\n--- 复合单元（吸纳 >=3 个 Gold 目标）---')
    for un in unames:
        if len(absorbed[un]) >= 3:
            print(f'  MERGE({len(absorbed[un])}): {un}')
            for gn in sorted(absorbed[un]):
                print(f'      <- {gn}')

    if args.report:
        p = Path(args.report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            'schema_version': 'maf-kg.knowledge-unit.gold.tolerant-check.v1',
            'gold': args.gold, 'ku': args.ku,
            'units_checked': len(units), 'fail_count': len(fails), 'warn_count': len(warns),
            'issues': issues,
            'absorbed': {k: sorted(v) for k, v in absorbed.items() if len(v) >= 2},
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nwrote report -> {p}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())