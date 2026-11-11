# -*- coding: utf-8 -*-
"""Prepare the separately-labelled progress-bounded iText2KG variant (merged material).

Follows the previous round's recipe (scripts/prepare_guarded_itext_retry.py):
take the frozen snapshot, replace the three guarded files from the project, set
itext_no_progress_limit=3, rebind artifact hashes and re-audit. The vendor tree is
junctioned (never copied or patched: the guard transforms the graph class in memory,
so the pinned upstream source hash still verifies). The shared iText ledger is reused,
so cumulative consumption is preserved and nothing is zeroed.
"""
import json, shutil, subprocess, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OLD_SNAP = NEW / 'snapshot'
OUT = NEW / 'guarded'
SNAP = OUT / 'snapshot'

if OUT.exists():
    raise SystemExit('guarded output dir already exists: %s' % OUT)
SNAP.mkdir(parents=True)

# light files: copy; heavy dirs: junction to the frozen originals
for name in ('agents', 'prompts'):
    shutil.copytree(OLD_SNAP / name, SNAP / name)
(SNAP / 'scripts').mkdir()
for src in sorted((OLD_SNAP / 'scripts').glob('*.py')):
    shutil.copy2(src, SNAP / 'scripts' / src.name)
for name in ('domain.json', 'embedding_lock.json', 'requirements.lock.txt'):
    if (OLD_SNAP / name).exists():
        shutil.copy2(OLD_SNAP / name, SNAP / name)
for name in ('vendor', 'runtime'):
    target = OLD_SNAP / name
    if target.exists():
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(SNAP / name), str(target)], check=False,
                       capture_output=True, text=True)

# swap in the guarded implementation (project scripts are the authoritative copies)
for name in ('external_itext2kg.py', 'run_external_baselines.py', 'itext_progress_guard.py'):
    shutil.copy2(ROOT / 'scripts' / name, SNAP / 'scripts' / name)
    print('guarded file  ->', name)

sys.path.insert(0, str(SNAP / 'scripts'))
import run_external_baselines as R  # noqa: E402
import run_direct_extraction_baseline as baseline  # noqa: E402

base = json.loads((NEW / 'ctl' / 'itext2kg' / 'manifest.json').read_text(encoding='utf-8'))
jobs = []
for repeat in (1, 2):
    jobs.append({'method': 'itext2kg', 'section': 'merged', 'repeat': repeat,
                 'directory': str(OUT / ('r%d' % repeat) / 'merged' / 'itext2kg'), 'status': 'pending'})

files = sorted((SNAP / 'scripts').glob('*.py')) + sorted((SNAP / 'agents').glob('*.py')) + \
        sorted((SNAP / 'prompts').glob('*.txt')) + [SNAP / 'domain.json', SNAP / 'embedding_lock.json'] + \
        sorted((NEW / 'input').glob('*.md')) + [NEW / 'input' / 'provenance.json', NEW / 'input' / 'sections.json',
                                                NEW / 'work' / 'merged_input.md'] + sorted((NEW / 'plans' / 'merged').glob('*.json'))

manifest = {
    'experiment': 'external-l1-native-method-merged-three-sections--itext-progress-bounded',
    'status': 'prepared',
    'snapshot': str(SNAP),
    'created_at': base.get('created_at'),
    'common': R.CONFIG,
    'reference': base['reference'],
    'sections': base['sections'],
    'jobs': jobs,
    'methods': ['itext2kg'],
    'shared_domain_instruction': base['shared_domain_instruction'],
    'embedding_lock': base['embedding_lock'],
    'per_method_budget_usd': base['per_method_budget_usd'],
    'per_method_token_limit': base['per_method_token_limit'],
    'budget_root': base['budget_root'],
    'task_max_calls': base['task_max_calls'],
    'task_max_seconds': base['task_max_seconds'],
    'session': base['session'],
    'itext_no_progress_limit': 3,
    'guard_authorization': ('User approved on 2026-10-09: the unguarded adapted iText2KG exhausts the 120-call ceiling on the '
                            'merged input, so the progress-bounded variant (retain isolated entities after 3 consecutive '
                            'no-progress passes, then continue) is run and reported separately, never pooled with the unguarded arm.'),
    'scope': ('One merged three-section document (two chapters), two repeats, iText2KG progress-bounded variant only. '
              'Separate label; shared cumulative iText ledger; unguarded outcome preserved.'),
    'input_serialization': base['input_serialization'],
}
manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
(NEW / 'guarded' / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

R.setup()
try:
    baseline.audit_prepared(manifest)
    print('guarded manifest AUDIT OK | jobs', [(j['repeat'], j['status']) for j in jobs],
          '| no_progress_limit', manifest['itext_no_progress_limit'])
except Exception as e:  # noqa: BLE001
    print('guarded manifest AUDIT FAIL:', type(e).__name__, e)
    raise SystemExit(1)
