# -*- coding: utf-8 -*-
"""Stop the unguarded iText r2 that started before the watcher fired, and record it.

r1 hit the frozen 120-call ceiling (preserved as evidence). r2 is cancelled by user
decision rather than left 'running'; its partial directory is kept, the abandoned
ledger reservation is settled conservatively (bound kept, never zeroed), and the
stale lock is removed.
"""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')

led = NEW / 'budgets' / 'itext2kg.json'
d = json.loads(led.read_text(encoding='utf-8'))
settled = 0
for r in d['requests']:
    if r.get('status') == 'reserved':
        r['status'] = 'unknown_usage_keep_full_reservation'
        r['note'] = ('unguarded iText r2 cancelled by user decision 2026-10-09 before settlement; '
                     'bound cost kept (conservative)')
        settled += 1
led.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
print('settled reservations:', settled, '| ledger tokens', d['accounting_tokens'], 'requests', len(d['requests']))

lock = NEW / 'budgets' / 'itext2kg.lock'
if lock.exists():
    lock.unlink()
    print('stale lock removed:', lock.name)

ctl = NEW / 'ctl' / 'itext2kg' / 'manifest.json'
m = json.loads(ctl.read_text(encoding='utf-8'))
for j in m['jobs']:
    if j['repeat'] == 2 and j['status'] == 'running':
        j['status'] = 'cancelled'
        j['cancelled_reason'] = ('unguarded arm stopped by user decision after r1 hit the 120-call ceiling; '
                                 'the progress-bounded variant runs separately and is never pooled with it')
        j['cancelled_approved_by'] = 'user 2026-10-09'
m['status'] = 'partial'
ctl.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
print('unguarded iText jobs now:', [(j['repeat'], j['status']) for j in m['jobs']])

r2dir = NEW / 'runs' / 'r2' / 'merged' / 'itext2kg'
print('partial r2 directory kept as evidence:', r2dir.exists())
