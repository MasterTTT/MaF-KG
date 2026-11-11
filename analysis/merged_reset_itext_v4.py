# -*- coding: utf-8 -*-
"""Clear the itext_v4 repeats that failed at import time and re-queue them.

The first launch used the system interpreter (D:\\ANACONDA\\python.exe), which has no `fastembed`, so
both workers died in `Embeddings.__init__` before any request: the ledger is unchanged and no run
directory holds anything but `stdout.log`. The experiment venv is
`evaluation/external_baselines_2026-10-08/runtime` (junctions point to it from every snapshot).

This script removes those two failed directories, resets the jobs to pending, and re-audits. It does
not touch the ledger.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = NEW / 'itext_v4'
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for job in manifest['jobs']:
        if job['status'] != 'failed':
            continue
        directory = Path(job['directory'])
        contents = sorted(p.name for p in directory.iterdir()) if directory.exists() else []
        if contents not in ([], ['stdout.log']):
            raise SystemExit('refusing to clear %s: unexpected contents %s' % (directory, contents))
        if directory.exists():
            shutil.rmtree(directory)
        job['status'] = 'pending'
        for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
            job.pop(key, None)
        print('cleared and re-queued r%d -> %s' % (job['repeat'], directory.name))
    manifest['status'] = 'prepared'
    manifest['launch_failure_note'] = ('First launch used the system interpreter, which lacks fastembed; both '
                                       'workers failed in Embeddings.__init__ before any request. Zero calls, '
                                       'ledger unchanged; re-queued under the experiment venv runtime/.')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    R.setup()
    baseline.audit_prepared(manifest)
    ledger = json.loads((NEW / 'budgets' / 'itext2kg.json').read_text(encoding='utf-8'))
    print('AUDIT OK | jobs %s | ledger %.4f usd / %d tokens | headroom %.4f usd / %d tokens'
          % ([(j['repeat'], j['status']) for j in manifest['jobs']], ledger['accounting_usd'],
             ledger['accounting_tokens'], ledger['usd_limit'] - ledger['accounting_usd'],
             ledger['token_limit'] - ledger['accounting_tokens']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
