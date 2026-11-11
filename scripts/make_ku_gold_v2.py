#!/usr/bin/env python
"""Derive KU Gold v2 from v1: relax merge constraints to textbook-section granularity.

Policy v2 (2026-08-27 复盘):
  * 放宽 11 对: 教材同一节内顺讲、合并后单元与教科书节粒度相当、且 L1 碎片化使细分无教学增益;
  * 保留 3 对: 跨节核心目标边界 (性质 vs 运算法则)、核心概念边界 (映射 vs 函数; 反函数 vs 复合函数)。

产出: evaluation/gold/ku_gold_ch1_v2.json (v1 保持原样不修改)。
"""
import json, sys
from pathlib import Path

SRC = Path('evaluation/gold/ku_gold_ch1_v1.json')
DST = Path('evaluation/gold/ku_gold_ch1_v2.json')

KEEP = {
    frozenset(['函数极限的基本性质', '极限的四则运算法则']),
    frozenset(['映射', '函数的基本概念']),
    frozenset(['反函数', '复合函数']),
}

RELAX_REASON = ('放宽：教材同一节内顺讲，合并后单元与教科书节/课时粒度相当；'
                'L1 节点碎片化使该细分对教学目标组织无额外增益。')

def main() -> int:
    gold = json.loads(SRC.read_text(encoding='utf-8'))
    pairs = gold.get('forbidden_merge_pairs', [])
    kept, relaxed = [], []
    for p in pairs:
        key = frozenset([p['ku_a'], p['ku_b']])
        if key in KEEP:
            kept.append(p)
        else:
            relaxed.append({**p, 'relaxed': True, 'relax_reason': RELAX_REASON})

    note = gold.get('note', '') + ('[v2 粒度政策] 禁合并对由 14 对调整为 3 对（保留跨节目标/核心概念边界），'
        '放宽 11 对至教材节/课时粒度；详情见 relaxed_merge_pairs 与 keep_merge_pairs_notes。')
    gold['schema_version'] = 'maf-kg.knowledge-unit.gold.v2'
    gold['note'] = note
    gold['granularity_policy_v2'] = {
        'granularity': '教材节/课时级中心教学目标',
        'kept_merge_pairs': [
            {'ku_a': p['ku_a'], 'ku_b': p['ku_b'], 'reason': p['reason']} for p in kept
        ],
        'relaxed_merge_pairs': [
            {'ku_a': p['ku_a'], 'ku_b': p['ku_b'], 'reason': p['reason'],
             'relax_reason': p.get('relax_reason', RELAX_REASON)} for p in relaxed
        ],
    }
    gold['forbidden_merge_pairs'] = kept
    DST.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding='utf-8')
    print('kept  :', len(kept))
    for p in kept:
        print('   -', p['ku_a'], '/', p['ku_b'])
    print('relaxed:', len(relaxed))
    for p in relaxed:
        print('   -', p['ku_a'], '/', p['ku_b'])
    print('wrote ->', DST)
    return 0

if __name__ == '__main__':
    sys.exit(main())