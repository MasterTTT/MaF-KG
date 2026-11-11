# -*- coding: utf-8 -*-
"""Dump v2 KU units with full member node info + flagged-node details."""
import json, sys

def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)

KU = load(r'E:\MaF-KG\KG\第一章_knowledge_units_context_v2.json')
G = load(r'E:\MaF-KG\KG\第一章_修复版_semantic_v1_7_0.json')

by_id = {n['id']: n for n in G['nodes']}

FLAGS = ['定义域', '值域', '收敛数列必有界', '零点定理', '介值定理', '逆映射', '复合映射']

def fmt(m):
    if isinstance(m, dict):
        return m.get('name'), m.get('id'), m.get('type')
    return str(m), None, None

print('#### 32 个单元成员明细（center 在前，contains 在后）####')
for u in KU['knowledge_units']:
    cs = u.get('centers_on', [])
    ms = u.get('contains', [])
    print()
    print('## [%d mem, %d ctr, %s] %s' % (len(ms), len(cs), u.get('aggregation_kind', '?'), u['name']))
    for m in cs:
        n, i, t = fmt(m)
        mark = ' <== 审查点' if n and any(f in n for f in FLAGS) else ''
        print('   [C][%s] %s (%s)%s' % (t or '?', n, i, mark))
    for m in ms:
        n, i, t = fmt(m)
        mark = ' <== 审查点' if n and any(f in n for f in FLAGS) else ''
        print('   [M][%s] %s (%s)%s' % (t or '?', n, i, mark))

print()
print('#### 审查点节点详情（L1 图内，含 description/evidence）####')
for n in G['nodes']:
    if any(f in n['name'] for f in FLAGS):
        ev = n.get('evidence')
        if isinstance(ev, list):
            ev = ev[0] if ev else ''
        print('* %s | %s | %s' % (n['type'], n['name'], n['id']))
        print('    desc: %s' % (n.get('description', '') or '')[:150])
        print('    ev  : %s' % (ev or ''))