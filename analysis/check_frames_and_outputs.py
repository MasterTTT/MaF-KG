# -*- coding: utf-8 -*-
"""Verify (a) line-number frame of packet evidence, (b) non-null item counts per run."""
import json, os, sys

sys.stdout.reconfigure(encoding='utf-8')
B = r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_2026-10-08'

print('=== snapshot layout ===')
for sub in ('input', 'source', 'scripts'):
    p = os.path.join(B, 'formal01', 'snapshot', sub)
    if os.path.isdir(p):
        print(' ', sub, '->', sorted(os.listdir(p))[:8])

print()
print('=== line-frame check: ch7_s4.md section lines 1-12 ===')
sec = open(os.path.join(B, 'formal01', 'snapshot', 'input', 'ch7_s4.md'), encoding='utf-8').read().splitlines()
for i, ln in enumerate(sec[:12], 1):
    print('  sec %3d | %s' % (i, ln[:90]))
chap_path = os.path.join(B, 'formal01', 'snapshot', 'source', '第七章_全章_无习题.md')
if os.path.exists(chap_path):
    chap = open(chap_path, encoding='utf-8').read().splitlines()
    print('  chapter file lines 973-984:')
    for i in range(972, min(984, len(chap))):
        print('  chp %4d | %s' % (i + 1, chap[i][:90]))

print()
print('=== non-null item counts per run (all 24 jobs) ===')
audit = json.load(open(os.path.join(B, 'formal01', 'execution_audit.json'), encoding='utf-8'))
rows = []
for j in audit['jobs']:
    d = j['directory']
    g = os.path.join(d, 'graph.json')
    nn = total = None
    if os.path.exists(g):
        try:
            obj = json.load(open(g, encoding='utf-8'))
            flat = []
            def walk(x):
                if isinstance(x, list):
                    for y in x:
                        walk(y)
                else:
                    flat.append(x)
            walk(obj)
            total = len(flat)
            nn = sum(1 for x in flat if x is not None)
        except Exception as e:  # noqa: BLE001
            nn, total = 'ERR', str(e)[:40]
    rows.append((j['method'], j['section'], j['repeat'], j['status'], nn, total))
for r in sorted(rows, key=lambda x: (x[1], x[2], x[0])):
    flag = '   <== EMPTY OUTPUT' if isinstance(r[4], int) and r[4] == 0 else ''
    print('  %-9s %-7s r%s  %-10s non_null=%-4s / slots=%-4s%s' % (r[0], r[1], r[2], r[3], r[4], r[5], flag))
