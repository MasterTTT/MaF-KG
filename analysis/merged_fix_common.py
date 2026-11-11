# -*- coding: utf-8 -*-
"""Align manifest['common'] with the runner's CONFIG (what setup() installs).

execute() calls setup(), which rebinds baseline.COMMON to CONFIG
(= run_memory_ablation.COMMON plus provider/transport keys). The audit then
compares the manifest against CONFIG, so the manifest must carry CONFIG.
"""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
SNAP = NEW / 'snapshot'
sys.path.insert(0, str(SNAP / 'scripts'))

import run_external_baselines as R  # noqa: E402

CONFIG = R.CONFIG
print('runner CONFIG max_tokens:', CONFIG.get('max_tokens'),
      '| keys:', len(CONFIG), '| extra vs COMMON:', sorted(set(CONFIG) - set(R.COMMON)))

for p in [NEW / 'manifest.json'] + [NEW / 'ctl' / m / 'manifest.json' for m in ('maf', 'direct', 'itext2kg', 'edc')]:
    mf = json.loads(p.read_text(encoding='utf-8'))
    mf['common'] = CONFIG
    p.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding='utf-8')

R.setup()  # installs baseline.COMMON = CONFIG, matching execute()
for m in ('maf', 'direct', 'itext2kg', 'edc'):
    mf = json.loads((NEW / 'ctl' / m / 'manifest.json').read_text(encoding='utf-8'))
    try:
        R.baseline.audit_prepared(mf)
        print('%-9s AUDIT OK' % m)
    except Exception as e:  # noqa: BLE001
        print('%-9s FAIL: %s' % (m, e))
