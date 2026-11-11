# -*- coding: utf-8 -*-
"""Evaluate the OLD (committed main-line) L2 KnowledgeUnit granularity.

Focus: 是否为"过细"粒度 —— 单元/节密度、成员规模分布、单成员/空单元、
小单元占比，以及各章节粒度一致性。纯统计，不改任何产物。
"""
import json, os, sys, collections

KG = r'E:\MaF-KG\KG'
sys.stdout.reconfigure(encoding='utf-8')

# 章 -> 节数（同济七版上册，--sections 治理后口径）
N_SECTIONS = {'第一章': 10, '第二章': 5, '第三章': 8,
              '第四章': 5, '第五章': 4, '第六章': 3, '第七章': 8}

FILES = {
    '第一章': os.path.join(KG, '第一章_knowledge_units_v7.json'),
    '第二章': os.path.join(KG, '第二章_knowledge_units_v1.json'),
    '第三章': os.path.join(KG, '第三章_knowledge_units_v1.json'),
    '第四章': os.path.join(KG, '第四章_knowledge_units_v1.json'),
    '第五章': os.path.join(KG, '第五章_knowledge_units_v1.json'),
    '第六章': os.path.join(KG, '第六章_knowledge_units_v1.json'),
    '第七章': os.path.join(KG, '第七章_knowledge_units_v1.json'),
}

def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)

def unit_centers(u):
    for k in ('centers_on', '_centers_on'):
        v = u.get(k)
        if isinstance(v, list):
            return v
    return []

def unit_contains(u):
    for k in ('contains', '_contains'):
        v = u.get(k)
        if isinstance(v, list):
            return v
    return []

def unit_mem_names(u):
    out = []
    for m in unit_centers(u) + unit_contains(u):
        if isinstance(m, dict):
            out.append(m.get('name', '?'))
        elif isinstance(m, str):
            out.append(m)
    return out

tot = collections.Counter()
print('=' * 76)
for ch, p in FILES.items():
    obj = load(p)
    units = obj['knowledge_units']
    sizes = [len(unit_contains(u)) for u in units]
    ctrs = [len(unit_centers(u)) for u in units]
    n_sect = N_SECTIONS[ch]
    print(f'## {ch}  单元 {len(units)}  节 {n_sect}  单元/节 {len(units)/n_sect:.1f}  '
          f'成员合计 {sum(sizes)}  均值 {sum(sizes)/max(len(sizes),1):.2f}  最大 {max(sizes) if sizes else 0}')
    hist = collections.Counter()
    for s in sizes:
        if s == 0: hist['0(空)'] += 1
        elif s == 1: hist['1'] += 1
        elif s <= 3: hist['2-3'] += 1
        elif s <= 6: hist['4-6'] += 1
        elif s <= 12: hist['7-12'] += 1
        else: hist['13+'] += 1
    print('   成员分布:', dict(hist))
    print('   中心数分布:', dict(collections.Counter(ctrs)))
    small = [(s, u) for s, u in zip(sizes, units) if s <= 2]
    print(f'   <=2 成员的小单元: {len(small)}')
    for s, u in sorted(small, key=lambda x: x[0]):
        print(f'      [{s} mem, {len(unit_centers(u))} ctr] {u["name"]}')
    tot['units'] += len(units); tot['empty'] += sizes.count(0)
    tot['s1'] += sizes.count(1); tot['s2'] += sizes.count(2)
    tot['members'] += sum(sizes)
    tot['sect'] += n_sect

print('=' * 76)
print('全书汇总: 单元', tot['units'], '| 成员', tot['members'],
      '| 单元/节 %.1f' % (tot['units'] / tot['sect']),
      '| 空单元', tot['empty'], '| 单成员', tot['s1'], '| 双成员', tot['s2'])

print()
print('=== 第一章 v7 全部 46 单元（成员数降序，看"一节拆几单元"）===')
obj = load(FILES['第一章'])
units = obj['knowledge_units']
for s, u in sorted([(len(unit_contains(u)), u) for u in units], key=lambda x: -x[0]):
    print('  [%2d mem %2d ctr] %s' % (s, len(unit_centers(u)), u['name']))