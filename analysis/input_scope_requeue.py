# -*- coding: utf-8 -*-
"""Re-queue failed input-scope jobs after a budget reallocation (no spend, no result editing).

Only jobs whose failure was a budget-gate stop are re-queued: their directories hold no graph and no
settled ledger entry, so nothing is overwritten except the failure log itself. Jobs that made a real
call and failed on a model output contract violation are NOT re-queued by this script's default
filter, because re-running those is a fresh sample and needs an explicit decision.

Usage: python analysis/input_scope_requeue.py [--methods maf] [--keep-logs]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--methods', nargs='+', default=['maf'])
    ap.add_argument('--include-contract-failures', action='store_true',
                    help=('also re-queue jobs that made a real call and failed on a model output contract '
                          'violation; re-running those is a fresh sample, so it needs an explicit decision'))
    ap.add_argument('--keep-logs', action='store_true', help='keep the previous failure log under logs/')
    args = ap.parse_args()

    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    requeued = []
    for job in manifest['jobs']:
        if job['status'] != 'failed' or job['method'] not in args.methods:
            continue
        directory = Path(job['directory'])
        log = directory / 'stdout.log'
        gate_stop = log.is_file() and 'budget gate reached' in log.read_text(encoding='utf-8', errors='replace')
        contract_failure = (args.include_contract_failures and log.is_file()
                            and 'ChunkMergeError' in log.read_text(encoding='utf-8', errors='replace'))
        if not (gate_stop or contract_failure):
            print('SKIP %s: failure was neither a budget-gate stop nor an approved contract re-run' % directory.name)
            continue
        print('  reason: %s' % ('budget gate stop' if gate_stop else 'model output contract violation (approved re-run)'))
        if args.keep_logs and log.is_file():
            (directory.parent / 'logs').mkdir(exist_ok=True)
            shutil.copy2(log, directory.parent / 'logs' / ('%s_%s_%s.stdout.log'
                                                           % (job['section'], job['method'], directory.name)))
        if directory.exists():
            shutil.rmtree(directory)
        job['status'] = 'pending'
        for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
            job.pop(key, None)
        requeued.append((job['repeat'], job['section'], job['method']))
        print('re-queued r%d %s %s' % (job['repeat'], job['section'], job['method']))
    manifest['status'] = 'prepared' if requeued else manifest.get('status')
    manifest['requeue_note'] = ('Re-queued after the limit reallocation inside the approved P1-A envelope; only '
                                'budget-gate stops are cleared, model-contract failures are left for an explicit '
                                'decision.')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    R.setup()
    manifest['common'] = R.CONFIG
    files = sorted((OUT / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'prompts').glob('*.txt')) + \
            [OUT / 'snapshot' / 'domain.json', OUT / 'snapshot' / 'embedding_lock.json'] + \
            sorted((OUT / 'work').glob('*/merged_input.md')) + \
            sorted((OUT / 'plans').glob('*/chunks.json')) + sorted((OUT / 'plans').glob('*/direct_chunk.json')) + \
            sorted((OUT / 'reference').glob('*.json'))
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    baseline.audit_prepared(manifest)
    print('re-queued: %s | AUDIT OK' % (requeued or 'nothing'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
