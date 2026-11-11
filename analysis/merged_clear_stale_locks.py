# -*- coding: utf-8 -*-
"""Clear stale per-method locks left by the aborted v1 workers, after verifying
the recorded PID is dead, and reset the affected control jobs to pending.

The gate is a lock file created with O_EXCL and unlinked in a finally block; a
killed process leaves it behind, which makes every later call raise GateReached.
"""
import json, os, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
logging = []
for method in ('maf', 'itext2kg', 'edc'):
    lock = NEW / 'budgets' / (method + '.lock')
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding='ascii').strip() or '0')
        except Exception:  # noqa: BLE001
            pid = 0
        alive = False
        if pid:
            try:
                os.kill(pid, 0)          # probe only
                alive = True
            except OSError:
                alive = False
        if alive:
            print('%-9s lock held by live pid %d -> NOT touched' % (method, pid))
            continue
        lock.unlink()
        print('%-9s stale lock (pid=%s dead) removed' % (method, pid or 'unknown'))
    else:
        print('%-9s no lock file' % method)
    p = NEW / 'ctl' / method / 'manifest.json'
    mf = json.loads(p.read_text(encoding='utf-8'))
    for job in mf['jobs']:
        job['status'] = 'pending'
        for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
            job.pop(key, None)
    p.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding='utf-8')
    # a failed job directory must not exist for execute()'s mkdir(exist_ok=False)
    for rep in ('r1', 'r2'):
        d = NEW / 'runs' / rep / 'merged' / method
        if d.exists():
            for child in sorted(d.rglob('*'), reverse=True):
                try:
                    child.unlink() if child.is_file() else child.rmdir()
                except OSError:
                    pass
            try:
                d.rmdir()
                print('           cleared partial dir runs/%s/merged/%s' % (rep, method))
            except OSError as e:
                print('           WARN could not fully clear %s: %s' % (d, e))
print()
print('direct lock kept (its arm is running).')
