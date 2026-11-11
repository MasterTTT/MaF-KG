# -*- coding: utf-8 -*-
"""Granularity statistics for KU products (main-line v7 vs 8/27 new pipeline)."""
import json, os, statistics, collections, re, sys

KG = r'E:\MaF-KG\KG'
sys.stdout.reconfigure(encoding='utf-8')

def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)

def find_units(obj):
    if isinstance(obj, list):
        return [u for u in obj if isinstance(u, dict)]
    cand = []
    for k in ('knowledge_units', 'units', 'kunits', 'data'):
        if k in obj and isinstance(obj[k], list):
            cand += [u for u in obj[k] if isinstance(u, dict)]
    if not cand:
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                if 'name' in v[0] or 'unit_name' in v[0] or 'objective' in v[0] or 'goal' in v[0]:
                    cand += v
    return cand

def name_of(u):
    return u.get('name') or u.get('unit_name') or u.get('objective') or u.get('goal') or '?'

def member_count(u):
    if u.get('member_count') is not None:
        return u['member_count']
    for k in ('members', 'member_ids', 'contains', 'node_ids', 'member_node_ids', 'nodes', 'member_names'):
        v = u.get(k)
        if isinstance(v, list):
            return len(v)
        if isinstance(v, dict):
            return len(v)
    return None

def member_names(u):
    for k in ('members', 'member_names'):
        v = u.get(k)
        if isinstance(v, list) and v:
            out = []
            for m in v:
                out.append(m.get('name') if isinstance(m, dict) else str(m))
            return out
    return None

def centers_of(u):
    for k in ('centers_on', 'center_nodes', 'center_node_ids', 'centers', 'center_ids', 'center'):
        v = u.get(k)
        if isinstance(v, list) and v:
            return len(v)
        if isinstance(v, dict) and v:
            return len(v)
    return None

COMP = re.compile(r'[、与及/和]')

def compound(name):
    return bool(COMP.search(name))

FILES = {
    'main_ch1': r'E:\MaF-KG\KG\第一章_knowledge_units_v1.json',
    'main_ch2': r'E:\MaF-KG\KG\第二章_knowledge_units_v1.json',
    'main_ch3': r'E:\MaF-KG\KG\第三章_knowledge_units_v1.json',
    'new_ch1':  r'E:\MaF-KG\KG\第一章_knowledge_units_context_v1.json',
    'new_ch2':  r'E:\MaF-KG\KG\第二章_knowledge_units_context_v1.json',
    'new_ch3':  r'E:\MaF-KG\KG\第三章_knowledge_units_context_v1.json',
    'new_ch1_v2': r'E:\MaF-KG\KG\第一章_knowledge_units_context_v2.json',
}

def report(label, path):
    obj = load(path)
    units = find_units(obj)
    print('=' * 72)
    print(label, '->', os.path.basename(path), '| units =', len(units))
    if not units:
        print('  (no units found; top keys:', list(obj.keys())[:10] if isinstance(obj, dict) else 'LIST', ')')
        return
    print('  first-unit keys:', list(units[0].keys()))
    sizes = [member_count(u) for u in units]
    alive = [s for s in sizes if s is not None]
    if len(alive) != len(sizes):
        print('  WARNING: %d units with unknown member count' % (len(sizes) - len(alive)))
    print('  sizes       :', alive)
    print('  total members:', sum(alive), '| min/mean/max/median:',
          min(alive), round(statistics.mean(alive), 2), max(alive), statistics.median(alive))
    hist = collections.Counter()
    for s in alive:
        if s <= 5: hist['<=5'] += 1
        elif s <= 10: hist['6-10'] += 1
        elif s <= 15: hist['11-15'] += 1
        else: hist['16+'] += 1
    print('  hist (members per unit):', dict(hist))
    cs = [centers_of(u) for u in units]
    cc = collections.Counter(c for c in cs if c is not None)
    print('  center-count distribution:', dict(cc), '(unknown:', len(cs) - sum(cc.values()), ')')
    names = [name_of(u) for u in units]
    print('  compound-name ratio: %d/%d' % (sum(1 for n in names if compound(n)), len(names)))
    big = sorted(zip(alive, units), key=lambda x: -x[0])[:3]
    for s, u in big:
        print('  BIG(%d): %s' % (s, name_of(u)))
        mn = member_names(u)
        if mn:
            print('     members[:8]:', mn[:8])
    if label in ('new_ch1', 'main_ch1'):
        print('  ALL units (size desc, centers):')
        for s, u in sorted(zip(alive, units), key=lambda x: -x[0]):
            print('    [%2d mem, %s ctr] %s' % (s, '-' if centers_of(u) is None else centers_of(u), name_of(u)))

for label, p in FILES.items():
    try:
        report(label, p)
    except Exception as e:
        print(label, 'ERROR', repr(e))

print('=' * 72)
print('GENERALIZATION (泛化测试) candidate files:')
for fn in sorted(os.listdir(KG)):
    if fn.startswith('泛化测试') and fn.endswith('.json'):
        try:
            obj = load(os.path.join(KG, fn))
            units = find_units(obj)
            print(' ', fn, '-> units found =', len(units))
        except Exception as e:
            print(' ', fn, 'ERROR', repr(e))

# Deep-dive: every unit of new_ch1 that involves 极限/重要极限/准则
print('=' * 72)
print('DEEP-DIVE new_ch1 merged units:')
obj = load(FILES['new_ch1'])
for u in find_units(obj):
    n = name_of(u)
    if any(k in n for k in ('极限', '准则', '无穷小', '连续')):
        print('  UNIT:', n)
        mn = member_names(u)
        if mn:
            for m in mn:
                print('      -', m)

print('=' * 72)
print('ALL per-chapter KU files (auto-discovered):')
for fn in sorted(os.listdir(KG)):
    if re.match(r'^第[一二三四五六七]章_knowledge_units.*\.json$', fn):
        try:
            units = find_units(load(os.path.join(KG, fn)))
            sz = [member_count(u) for u in units if member_count(u) is not None]
            if units:
                print(' ', fn, '| units:', len(units), '| members:', sum(sz),
                      '| mean/max:', (round(sum(sz) / len(sz), 1), max(sz)) if sz else '-')
        except Exception as e:
            print(' ', fn, 'ERR', repr(e))

print('=' * 72)
print('UNION COVERAGE of context_v1 files (output membership vs chapter graph):')
for ch in ('第一章', '第二章', '第三章'):
    p = os.path.join(KG, ch + '_knowledge_units_context_v1.json')
    obj = load(p)
    units = find_units(obj)
    ids = set()
    for u in units:
        for k in ('contains', 'centers_on', 'members', 'node_ids', 'center_node_ids'):
            v = u.get(k)
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, dict):
                        x = x.get('id')
                    if x:
                        ids.add(x)
    gp = os.path.join(KG, ch + '_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json')
    gnodes = set()
    if os.path.exists(gp):
        for n in load(gp).get('nodes', []):
            gnodes.add(n.get('id'))
    print(' ', ch, '| member ids (union):', len(ids),
          '| graph nodes:', len(gnodes),
          '| in graph:', len(ids & gnodes),
          '| outside graph:', len(ids - gnodes),
          '| graph nodes uncovered:', len(gnodes - ids))

print('=' * 72)
print('EMPTY / OVER-CAP units in context_v1 files:')
for ch in ('第一章', '第二章', '第三章'):
    p = os.path.join(KG, ch + '_knowledge_units_context_v1.json')
    for u in find_units(load(p)):
        ctr = u.get('centers_on') or []
        if member_count(u) == 0:
            print('  EMPTY:', ch, u.get('id'), u.get('name'), '| centers_on =', ctr)
        if len(ctr) > 3:
            print('  >3 centers:', ch, u.get('id'), u.get('name'), '| n_centers =', len(ctr))