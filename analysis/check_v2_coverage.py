# -*- coding: utf-8 -*-
"""Independent coverage check for 第一章_knowledge_units_context_v2.json."""
import json, sys

def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)

ku = load(r'E:\MaF-KG\KG\第一章_knowledge_units_context_v2.json')
g = load(r'E:\MaF-KG\KG\第一章_修复版_semantic_v1_7_0.json')

ids = set()
zero_contains = 0
zero_centers = 0
for u in ku['knowledge_units']:
    for k in ('contains', 'centers_on'):
        for m in u.get(k, []):
            if isinstance(m, dict):
                if m.get('id'):
                    ids.add(m['id'])
            elif isinstance(m, str):
                ids.add(m)
    if not u.get('contains'):
        zero_contains += 1
        if not u.get('centers_on'):
            zero_centers += 1
            print('  TOTALLY EMPTY unit:', u.get('name'), u.get('id'))

gnodes = {n['id'] for n in g['nodes']}
print('context_v2 units:', len(ku['knowledge_units']))
print('member ids (union contains+centers):', len(ids))
print('graph nodes:', len(gnodes))
print('members outside graph:', len(ids - gnodes))
print('graph nodes uncovered:', len(gnodes - ids))
print('units with empty contains:', zero_contains, '| of which empty centers too:', zero_centers)