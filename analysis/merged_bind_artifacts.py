# -*- coding: utf-8 -*-
"""Bind frozen artifacts into the merged experiment manifest and run the official audit."""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
SNAP = NEW / 'snapshot'
sys.path.insert(0, str(SNAP / 'scripts'))

import run_direct_extraction_baseline as baseline  # noqa: E402

files = sorted((SNAP / 'scripts').glob('*.py')) + sorted((SNAP / 'agents').glob('*.py')) + \
        sorted((SNAP / 'prompts').glob('*.txt')) + [SNAP / 'domain.json', SNAP / 'embedding_lock.json'] + \
        sorted((NEW / 'input').glob('*.md')) + [NEW / 'input' / 'provenance.json', NEW / 'input' / 'sections.json',
                                                NEW / 'work' / 'merged_input.md'] + sorted((NEW / 'plans' / 'merged').glob('*.json'))
manifest = json.loads((NEW / 'manifest.json').read_text(encoding='utf-8'))
manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
(NEW / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print('bound artifacts:', len(manifest['artifacts']))
print('common.max_tokens in manifest:', manifest['common'].get('max_tokens'), '| COMMON in snapshot:', baseline.COMMON.get('max_tokens'))

try:
    baseline.audit_prepared(manifest)
    print('AUDIT PREPARED: OK')
except Exception as e:  # noqa: BLE001
    print('AUDIT PREPARED: FAIL ->', type(e).__name__, e)
    raise SystemExit(1)
