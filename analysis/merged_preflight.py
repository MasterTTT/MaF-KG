# -*- coding: utf-8 -*-
"""Preflight for the merged three-section baseline experiment.

Slices the three sections from the canonical no-exercise chapter files, reports
size, and extrapolates a single-request (Direct) output budget from the previous
formal01 runs' actual token usage.
"""
import json, os, sys

sys.stdout.reconfigure(encoding='utf-8')
RAW = r'E:\MaF-KG\data\raw'
OLD = r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_2026-10-08'

SECTIONS = [
    ('ch2_s5', '函数的微分', '第二章_全章_无习题.md', 1702, None),
    ('ch3_s1', '微分中值定理', '第三章_全章_无习题.md', 5, 366),
    ('ch3_s2', '洛必达法则', '第三章_全章_无习题.md', 367, 524),
]

total_chars = total_lines = 0
print('=== section slices ===')
for sid, title, fname, a, b in SECTIONS:
    lines = open(os.path.join(RAW, fname), encoding='utf-8').read().splitlines()
    lo = a - 1
    hi = (b if b else len(lines))
    body = lines[lo:hi]
    text = '\n'.join(body)
    chars = len(text)
    total_chars += chars
    total_lines += len(body)
    print('  %-7s %-12s lines %4d-%-4d  lines=%4d  chars=%6d  est_tokens~%5d' % (
        sid, title, a, hi, len(body), chars, int(chars / 1.6)))
print('  TOTAL lines=%d chars=%d est_tokens~%d' % (total_lines, total_chars, int(total_chars / 1.6)))

print()
print('=== previous formal01 per-request usage (baseline for extrapolation) ===')
audit = json.load(open(os.path.join(OLD, 'formal01', 'execution_audit.json'), encoding='utf-8'))
for m in ('direct', 'maf', 'itext2kg', 'edc'):
    jobs = [j for j in audit['jobs'] if j['method'] == m]
    req = sum(j.get('actual_requests', 0) or 0 for j in jobs)
    u = {'completion_tokens': 0, 'reasoning_tokens': 0, 'prompt_tokens': 0}
    for j in jobs:
        nu = j.get('native_usage') or {}
        for k in u:
            u[k] += nu.get(k, 0) or 0
    done = sum(1 for j in jobs if j['status'] == 'completed')
    print('  %-9s runs=%d completed=%d requests=%d | prompt=%d completion=%d reasoning=%d | '
          'completion/request=%.0f reasoning/request=%.0f visible_out/request=%.0f' % (
              m, len(jobs), done, req, u['prompt_tokens'], u['completion_tokens'], u['reasoning_tokens'],
              u['completion_tokens'] / req if req else 0, u['reasoning_tokens'] / req if req else 0,
              (u['completion_tokens'] - u['reasoning_tokens']) / req if req else 0))

print()
print('=== single-section reference size (previous experiment) ===')
sizes = []
for sid, title, fname, a, b in SECTIONS:
    lines = open(os.path.join(RAW, fname), encoding='utf-8').read().splitlines()
    lo, hi = a - 1, (b if b else len(lines))
    sizes.append((sid, len('\n'.join(lines[lo:hi]))))
avg_single = sum(s for _, s in sizes if _ != 'x') / len(sizes)
for sid, s in sizes:
    print('  %-7s chars=%-6d  ratio_vs_smallest=%.2f' % (sid, s, s / min(x for _, x in sizes)))
print()
print('NOTE: Direct issues exactly one request per input document under the frozen config')
print('      (24,000 max output tokens per request, thinking enabled -> reasoning counts toward it).')
