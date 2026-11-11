# -*- coding: utf-8 -*-
"""Create one control directory per method so the four arms can run in parallel.

Each control manifest carries only that method's jobs and its own output path, so
concurrent `execute` runs never write the same manifest.json. Job directories,
snapshot, plans, budgets and reference stay shared and read-only.
"""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
manifest = json.loads((NEW / 'manifest.json').read_text(encoding='utf-8'))
for method in ('maf', 'direct', 'itext2kg', 'edc'):
    ctl = NEW / 'ctl' / method
    ctl.mkdir(parents=True, exist_ok=True)
    m = json.loads(json.dumps(manifest))
    m['jobs'] = [j for j in manifest['jobs'] if j['method'] == method]
    m['status'] = 'prepared'
    m['control_for'] = method
    (ctl / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    print('%-9s control manifest -> %s | jobs=%s' % (
        method, ctl / 'manifest.json', [(j['repeat'], j['status']) for j in m['jobs']]))
print()
print('reference file recorded in manifest:', manifest['reference'])
print('per-method budget root:', manifest['budget_root'])
