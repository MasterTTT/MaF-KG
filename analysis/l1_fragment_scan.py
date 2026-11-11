# -*- coding: utf-8 -*-
"""Scan L1 graphs for over-fine fragment nodes (near-duplicate / twin names).

Evidence list for "layer-1 节点粒度过细" (决策 #3: L1 语义合并).
"""
import json, os, sys, itertools

def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)

def bigrams(s):
    s = ''.join(s.split())
    return set(s[i:i+2] for i in range(len(s)-1))

def jac(a, b):
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)

FILES = [
    (r'E:\MaF-KG\KG\第一章_修复版_semantic_v1_7_0.json', 'Ch1 修复版 (197 nodes, 全一章)'),
    (r'E:\MaF-KG\KG\第三章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json', 'Ch1-3 累积 (418 nodes)'),
]

for path, label in FILES:
    g = load(path)
    nodes = [n for n in g.get('nodes', []) if n.get('type') in ('Claim', 'Concept')]
    pairs = []
    for a, b in itertools.combinations(nodes, 2):
        if a['name'] == b['name']:
            continue
        s = jac(a['name'], b['name'])
        if s >= 0.5:
            pairs.append((s, a, b))
    pairs.sort(key=lambda x: -x[0])
    print('=' * 72)
    print(label, '| claim/concept nodes:', len(nodes), '| similar name pairs (>=0.5):', len(pairs))
    for s, a, b in pairs[:50]:
        print('  %.2f  [%s] %s' % (s, a['type'], a['name']))
        print('        [%s] %s' % (b['type'], b['name']))